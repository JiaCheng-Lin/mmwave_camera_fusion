import numpy as np
import cv2 as cv
import copy
import glob

imgs = []

camera_params = np.load("./intrinsic_parameters/camera_parameters_202211240103.npy", allow_pickle=True)[()]
mtx = np.array(camera_params['K'])
dist = np.array(camera_params['dist'])

print(mtx)
print(dist)

for i in range(-10, 10, 1):
    print(i)
    points_2d = cv.projectPoints(np.array([i/10, 0.0, 0.0]), np.array([0.0,0.0,0.0]), np.array([0.0,0.0,0.0]), mtx, dist)[0]
    # https://docs.opencv.org/3.4/d9/d0c/group__calib3d.html#ga1019495a2c8d1743ed5cc23fa0daff8c

    print(points_2d)

# imgs_path = glob.glob("./1123/*.jpg")
# for im_path in imgs_path:
#     img_ori = cv.imread(im_path)
#     cv.imshow("ori",img_ori)

#     img = copy.deepcopy(img_ori)
#     # imgs.append(img)
#     h,  w = img.shape[:2]
#     newcameramtx, roi = cv.getOptimalNewCameraMatrix(mtx, dist, (w,h), 1, (w,h))
#     # undistort
#     dst = cv.undistort(img, mtx, dist, None, newcameramtx)
#     # crop the image
#     x, y, w, h = roi    
#     dst = dst[y:y+h, x:x+w]
#     cv.imshow("dst", dst)

#     # undistort
#     mapx, mapy = cv.initUndistortRectifyMap(mtx, dist, None, newcameramtx, (w,h), 5)
#     dst_1 = cv.remap(img, mapx, mapy, cv.INTER_LINEAR)
#     # crop the image
#     x, y, w, h = roi
#     dst_1 = dst_1[y:y+h, x:x+w]

#     cv.imshow("dst_1", dst_1)
    
#     cv.waitKey(0)

# cv.destroyAllWindows()