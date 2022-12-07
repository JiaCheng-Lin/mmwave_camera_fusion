import argparse
import os
import os.path as osp
import time
import cv2
import torch

from loguru import logger

from yolox.data.data_augment import preproc
from yolox.exp import get_exp
from yolox.utils import fuse_model, get_model_info, postprocess
from yolox.utils.visualize import plot_tracking
from yolox.tracker.byte_tracker import BYTETracker
from yolox.tracking_utils.timer import Timer

"""mmwave""" 
from socket import *
from datetime import datetime
import json
# import time
import numpy as np

import sys
sys.path.append("../inference/")
from mmwave_utils.mmwave import * # import mmwave utils (functions)
from mmwave_utils.mmwave_pts_visualization import *
import copy
"""mmwave""" 

IMAGE_EXT = [".jpg", ".jpeg", ".webp", ".bmp", ".png"]


def make_parser():
    parser = argparse.ArgumentParser("ByteTrack Demo!")
    parser.add_argument(
        "demo", default="image", help="demo type, eg. image, video and webcam"
    )
    parser.add_argument("-expn", "--experiment-name", type=str, default=None)
    parser.add_argument("-n", "--name", type=str, default=None, help="model name")

    parser.add_argument(
        #"--path", default="./datasets/mot/train/MOT17-05-FRCNN/img1", help="path to images or video"
        "--path", default="./videos/video_11_46_24.avi", help="path to images or video"
    )
    parser.add_argument("--camid", type=int, default=0, help="webcam demo camera id")
    parser.add_argument(
        "--save_result",
        action="store_true",
        help="whether to save the inference result of image/video",
    )

    ### DEFAULT: "bytetrack_x_mot17" 
    # exp file
    parser.add_argument(
        "-f",
        "--exp_file",
        default=r'C:\TOBY\jorjin\object_tracking\ByteTrack\exps\example\mot/yolox_x_mix_det.py',
        type=str,
        help="pls input your expriment description file",
    )
    parser.add_argument(
        "-c", 
        "--ckpt", 
        default=r'C:\TOBY\jorjin\object_tracking\ByteTrack\pretrained/bytetrack_x_mot17.pth.tar', 
        type=str, 
        help="ckpt for eval"
    )
    parser.add_argument(
        "--device",
        default="gpu",
        type=str,
        help="device to run our model, can either be cpu or gpu",
    )
    parser.add_argument("--conf", default=None, type=float, help="test conf")
    parser.add_argument("--nms", default=None, type=float, help="test nms threshold")
    parser.add_argument("--tsize", default=None, type=int, help="test img size")
    parser.add_argument("--fps", default=30, type=int, help="frame rate (fps)")
    parser.add_argument(
        "--fp16",
        dest="fp16",
        default=False,
        action="store_true",
        help="Adopting mix precision evaluating.",
    )
    parser.add_argument(
        "--fuse",
        dest="fuse",
        default=False,
        action="store_true",
        help="Fuse conv and bn for testing.",
    )
    parser.add_argument(
        "--trt",
        dest="trt",
        default=False,
        action="store_true",
        help="Using TensorRT model for testing.",
    )
    # tracking args
    parser.add_argument("--track_thresh", type=float, default=0.5, help="tracking confidence threshold")
    parser.add_argument("--track_buffer", type=int, default=30, help="the frames for keep lost tracks")
    parser.add_argument("--match_thresh", type=float, default=0.8, help="matching threshold for tracking")
    parser.add_argument(
        "--aspect_ratio_thresh", type=float, default=1.6,
        help="threshold for filtering out boxes of which aspect ratio are above the given value."
    )
    parser.add_argument('--min_box_area', type=float, default=10, help='filter out tiny boxes')
    parser.add_argument("--mot20", dest="mot20", default=False, action="store_true", help="test mot20.")
    return parser


def get_image_list(path):
    image_names = []
    for maindir, subdir, file_name_list in os.walk(path):
        for filename in file_name_list:
            apath = osp.join(maindir, filename)
            ext = osp.splitext(apath)[1]
            if ext in IMAGE_EXT:
                image_names.append(apath)
    return image_names


def write_results(filename, results):
    save_format = '{frame},{id},{x1},{y1},{w},{h},{s},-1,-1,-1\n'
    with open(filename, 'w') as f:
        for frame_id, tlwhs, track_ids, scores in results:
            for tlwh, track_id, score in zip(tlwhs, track_ids, scores):
                if track_id < 0:
                    continue
                x1, y1, w, h = tlwh
                line = save_format.format(frame=frame_id, id=track_id, x1=round(x1, 1), y1=round(y1, 1), w=round(w, 1), h=round(h, 1), s=round(score, 2))
                f.write(line)
    logger.info('save results to {}'.format(filename))


class Predictor(object):
    def __init__(
        self,
        model,
        exp,
        trt_file=None,
        decoder=None,
        device=torch.device("cpu"),
        fp16=False
    ):
        self.model = model
        self.decoder = decoder
        self.num_classes = exp.num_classes
        self.confthre = exp.test_conf
        self.nmsthre = exp.nmsthre
        self.test_size = exp.test_size
        self.device = device
        self.fp16 = fp16
        if trt_file is not None:
            from torch2trt import TRTModule

            model_trt = TRTModule()
            model_trt.load_state_dict(torch.load(trt_file))

            x = torch.ones((1, 3, exp.test_size[0], exp.test_size[1]), device=device)
            self.model(x)
            self.model = model_trt
        self.rgb_means = (0.485, 0.456, 0.406)
        self.std = (0.229, 0.224, 0.225)

    def inference(self, img, timer):
        img_info = {"id": 0}
        if isinstance(img, str):
            img_info["file_name"] = osp.basename(img)
            img = cv2.imread(img)
        else:
            img_info["file_name"] = None

        height, width = img.shape[:2]
        img_info["height"] = height
        img_info["width"] = width
        img_info["raw_img"] = img

        img, ratio = preproc(img, self.test_size, self.rgb_means, self.std)
        img_info["ratio"] = ratio
        img = torch.from_numpy(img).unsqueeze(0).float().to(self.device)
        if self.fp16:
            img = img.half()  # to FP16

        with torch.no_grad():
            timer.tic()
            outputs = self.model(img)
            if self.decoder is not None:
                outputs = self.decoder(outputs, dtype=outputs.type())
            outputs = postprocess(
                outputs, self.num_classes, self.confthre, self.nmsthre
            )
            #logger.info("Infer time: {:.4f}s".format(time.time() - t0))
        return outputs, img_info


def image_demo(predictor, vis_folder, current_time, args):
    if osp.isdir(args.path):
        files = get_image_list(args.path)
    else:
        files = [args.path]
    files.sort()
    tracker = BYTETracker(args, frame_rate=args.fps)
    timer = Timer()
    results = []

    for frame_id, img_path in enumerate(files, 1):
        outputs, img_info = predictor.inference(img_path, timer)
        if outputs[0] is not None:
            online_targets = tracker.update(outputs[0], [img_info['height'], img_info['width']], exp.test_size)
            online_tlwhs = []
            online_ids = []
            online_scores = []
            for t in online_targets:
                tlwh = t.tlwh
                tid = t.track_id
                vertical = tlwh[2] / tlwh[3] > args.aspect_ratio_thresh
                if tlwh[2] * tlwh[3] > args.min_box_area and not vertical:
                    online_tlwhs.append(tlwh)
                    online_ids.append(tid)
                    online_scores.append(t.score)
                    # save results
                    results.append(
                        f"{frame_id},{tid},{tlwh[0]:.2f},{tlwh[1]:.2f},{tlwh[2]:.2f},{tlwh[3]:.2f},{t.score:.2f},-1,-1,-1\n"
                    )
            timer.toc()
            online_im = plot_tracking(
                img_info['raw_img'], online_tlwhs, online_ids, frame_id=frame_id, fps=1. / timer.average_time
            )
        else:
            timer.toc()
            online_im = img_info['raw_img']

        # result_image = predictor.visual(outputs[0], img_info, predictor.confthre)
        if args.save_result:
            timestamp = time.strftime("%Y_%m_%d_%H_%M_%S", current_time)
            save_folder = osp.join(vis_folder, timestamp)
            os.makedirs(save_folder, exist_ok=True)
            cv2.imwrite(osp.join(save_folder, osp.basename(img_path)), online_im)

        if frame_id % 20 == 0:
            logger.info('Processing frame {} ({:.2f} fps)'.format(frame_id, 1. / max(1e-5, timer.average_time)))

        ch = cv2.waitKey(0)
        if ch == 27 or ch == ord("q") or ch == ord("Q"):
            break

    if args.save_result:
        res_file = osp.join(vis_folder, f"{timestamp}.txt")
        with open(res_file, 'w') as f:
            f.writelines(results)
        logger.info(f"save results to {res_file}")

def distance_finder(focal_length, real_object_width, width_in_frame): # fake distance finder
    distance = (real_object_width * focal_length) / width_in_frame
    return distance

def get_center_pt_list(online_ids, online_tlwhs):
    # center_pt_list = [] # [[centerPt_u, centerPt_v], ]

    if len(online_ids) != 1: # num_person in webcam must be 1: easy to record.
        return [] 
    for idx, tlwh in enumerate(online_tlwhs):
        center_pt_x, center_pt_y = int(tlwh[0]+tlwh[2]/2), int(tlwh[1]+tlwh[3]/2)
        
        return [center_pt_x, int(tlwh[3])] # original: center_pt_xy
                                           # current: [mid_x, bottom_y]

    return []

def get_origin_mmwave_pts(mmwave_json, origin_px=6.0, origin_py=1.0): # # origin_px/py: jorjin Device original point 
    xy_list = [] # px, py

    detection = int(mmwave_json["Detection"]) # # number of person

    # if detection != 1: # num_person in mmwave must be 1: easy to record.
    #     return [] 

    for i in range(detection): 
        ID, px, py = mmwave_json["JsonTargetList"][i]["ID"], \
                    round(mmwave_json["JsonTargetList"][i]["Px"]-origin_px, 5), \
                    round(mmwave_json["JsonTargetList"][i]["Py"]-origin_py, 5) # minus the origin_x & y
        
        xy_list.append([px, py])
        
    return xy_list

## process the video or webcam flow
def imageflow_demo(predictor, vis_folder, current_time, args):
    cap = cv2.VideoCapture(args.path if args.demo == "video" else args.camid)
    width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)  # float
    height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)  # float
    fps = cap.get(cv2.CAP_PROP_FPS)
    timestamp = time.strftime("%Y_%m_%d_%H_%M_%S", current_time)
    save_folder = osp.join(vis_folder, timestamp)
    if args.save_result:
        os.makedirs(save_folder, exist_ok=True)
    if args.demo == "video":
        save_path = osp.join(save_folder, args.path.split("/")[-1])
    else:
        save_path = osp.join(save_folder, "camera.mp4")
    logger.info(f"video save_path is {save_path}")
    vid_writer = cv2.VideoWriter(
        save_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (int(width), int(height))
    )
    if args.demo == "webcam": 
        origin_vid_writer = cv2.VideoWriter(
            osp.join(save_folder, "origin_camera.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, (int(width), int(height))
        )
    tracker = BYTETracker(args, frame_rate=30)
    timer = Timer()
    frame_id = 0
    results = []
    mmwave_json = None # initialize mmwave_json data
    previous_ID_matches  = [] # record previous ID matches

    # # read mmwave background image
    bg = cv2.imread(r"C:\TOBY\jorjin\MMWave\mmwave_webcam_fusion\inference\byteTrack_mmwave\inference\mmwave_utils/mmwave_bg.png")
    
    cooresponding_pts = []
    save_name = "data_"+str(datetime.now().strftime("%Y_%m_%d_%H_%M_%S")) + '.npy'
    data_save_path = './data/' + save_name

    camera_params = np.load("./getK/intrinsic_parameters/camera_parameters_202211240103.npy", allow_pickle=True)[()]
    mtx = np.array(camera_params['K'])
    dist = np.array(camera_params['dist'])

    while True:
        if frame_id % 20 == 0:
            logger.info('Processing frame {} ({:.2f} fps)'.format(frame_id, 1. / max(1e-5, timer.average_time)))
        ret_val, frame = cap.read()
        # cv2.imshow('frame', frame)
        
        
        """get mmwave data"""
        if frame_id != 0 : # reason: (initializing img model)Let the image be processed a frame. Otherwise, the time error will be very large 
            mm_time_error, mmwave_json = mmwave_data_process(frame_id, mmwave_json)
            
            
            if mm_time_error >= 0.1: # time error > 100 ms -> no match -> continue
                # cv2.putText(frame, "mm_time_error:"+str(mm_time_error),  \
                #             (30, 30), cv2.FONT_HERSHEY_COMPLEX_SMALL, \
                #             0.8, (255, 255, 0), 1, cv2.LINE_AA)
                # cv2.imshow('frame', frame)
                continue # if time_error > threshold (img speed > mmwave speed), skip this img.
        """get mmwave data"""

        if ret_val:
            outputs, img_info = predictor.inference(frame, timer)
            if outputs[0] is not None:
                online_targets = tracker.update(outputs[0], [img_info['height'], img_info['width']], exp.test_size)
                online_tlwhs = []
                online_ids = []
                online_scores = []
                for t in online_targets:
                    tlwh = t.tlwh
                    tid = t.track_id
                    vertical = tlwh[2] / tlwh[3] > args.aspect_ratio_thresh
                    if tlwh[2] * tlwh[3] > args.min_box_area and not vertical:
                        online_tlwhs.append(tlwh)
                        online_ids.append(tid)
                        online_scores.append(t.score)
                        results.append(
                            f"{frame_id},{tid},{tlwh[0]:.2f},{tlwh[1]:.2f},{tlwh[2]:.2f},{tlwh[3]:.2f},{t.score:.2f},-1,-1,-1\n"
                        )
                timer.toc()
                online_im = plot_tracking(
                    img_info['raw_img'], online_tlwhs, online_ids, frame_id=frame_id + 1, fps=1. / timer.average_time
                )
            else:
                timer.toc()
                online_im = img_info['raw_img']

            # print("online_ids", online_ids)
            # print("online_tlwhs", online_tlwhs)
            """!!! mmwave process !!!"""
            if frame_id != 0 :
                
                """ get mmwave origin pts(x, y in real world) data """
                # # xy_list: [px, py]
                origin_xy_list = get_origin_mmwave_pts(mmwave_json, origin_px=6.0, origin_py=1.0)
                if origin_xy_list:
                    for pt in origin_xy_list:
                        x, z = pt[0], pt[1]
                        points_2d = cv2.projectPoints(np.array([-x, 0.0, z]), np.array([0.0,0.0,0.0]), np.array([0.0,0.0,0.0]), mtx, dist)[0]
                        print(tuple(points_2d.flatten()))
                        a = points_2d.flatten()
                        cv2.circle(online_im,(int(a[0]), int(a[1])),1,(0,0,255),8)

                
                """ get person center x y in img"""
                # # center_pt_list: [center_pt_x, center_pt_y]
                center_pt_list = get_center_pt_list(online_ids, online_tlwhs)
                
                # the num_person in img == 1 and the num_person in mmwave == 1
                if origin_xy_list and center_pt_list: # not none
                    # print(center_pt_list)
                    # print(origin_xy_list)                  
                    # print(center_pt_list + origin_xy_list)
                    cooresponding_pts.append(center_pt_list + origin_xy_list)

                    # print(cooresponding_pts)
            """!!! mmwave process !!!"""
            
            if args.save_result:
                vid_writer.write(online_im)
                if args.demo == "webcam": 
                    origin_vid_writer.write(frame)
            cv2.imshow("online_im", online_im)
            ch = cv2.waitKey(1)
            if ch == 27 or ch == ord("q") or ch == ord("Q"):
                # save data
                if args.save_result:
                    with open(data_save_path, 'wb+') as f:
                        np.save(f, np.array(cooresponding_pts))
                    f.close()

                break
        else:
            break
        frame_id += 1

    if args.save_result:
        res_file = osp.join(vis_folder, f"{timestamp}.txt")
        with open(res_file, 'w') as f:
            f.writelines(results)
        logger.info(f"save results to {res_file}")


def main(exp, args):
    if not args.experiment_name:
        args.experiment_name = exp.exp_name

    output_dir = osp.join("../"+exp.output_dir, args.experiment_name)
    os.makedirs(output_dir, exist_ok=True)

    vis_folder = osp.join(output_dir, "track_vis")
    if args.save_result:
        os.makedirs(vis_folder, exist_ok=True)

    if args.trt:
        args.device = "gpu"
    args.device = torch.device("cuda" if args.device == "gpu" else "cpu")

    logger.info("Args: {}".format(args))

    if args.conf is not None:
        exp.test_conf = args.conf
    if args.nms is not None:
        exp.nmsthre = args.nms
    if args.tsize is not None:
        exp.test_size = (args.tsize, args.tsize)

    model = exp.get_model().to(args.device)
    logger.info("Model Summary: {}".format(get_model_info(model, exp.test_size)))
    model.eval()

    if not args.trt:
        if args.ckpt is None:
            ckpt_file = osp.join(output_dir, "best_ckpt.pth.tar")
        else:
            ckpt_file = args.ckpt
        logger.info("loading checkpoint")
        ckpt = torch.load(ckpt_file, map_location="cpu")

        # if "head.reid_classifier.weight" in ckpt["model"]:  # TODO: remove checkpoint of ReID classifier
        #     ckpt["model"].pop("head.reid_classifier.weight")
        # if "head.reid_classifier.bias" in ckpt["model"]:  # TODO: remove checkpoint of ReID classifier
        #     ckpt["model"].pop("head.reid_classifier.bias")
        # model.load_state_dict(ckpt["model"], strict=False)  # TODO: set strict=False for missing keys of classifier

        # load the model state dict
        model.load_state_dict(ckpt["model"]) # original 
        logger.info("loaded checkpoint done.")

    if args.fuse:
        logger.info("\tFusing model...")
        model = fuse_model(model)

    if args.fp16:
        model = model.half()  # to FP16

    if args.trt:
        assert not args.fuse, "TensorRT model is not support model fusing!"
        trt_file = osp.join(output_dir, "model_trt.pth")
        assert osp.exists(
            trt_file
        ), "TensorRT model is not found!\n Run python3 tools/trt.py first!"
        model.head.decode_in_inference = False
        decoder = model.head.decode_outputs
    else:
        trt_file = None
        decoder = None

    predictor = Predictor(model, exp, trt_file, decoder, args.device, args.fp16)
    current_time = time.localtime()
    if args.demo == "image":
        image_demo(predictor, vis_folder, current_time, args)
    elif args.demo == "video" or args.demo == "webcam":
        imageflow_demo(predictor, vis_folder, current_time, args)


if __name__ == "__main__":
    args = make_parser().parse_args()
    exp = get_exp(args.exp_file, args.name)

    main(exp, args)
