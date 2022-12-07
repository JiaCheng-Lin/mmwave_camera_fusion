# camera_calibration (intrinsic parameters)


### 1.  See **getK** folder to get intrinsic parameters. 
Cal the intrinsic parameters by checkerboard img.

<br>

### 2. Inference. 
Map/project the radar pts (received from jorjin mmwave radar) to image plane (captured from webcam) by intrinsic parameters. 

```bash
python vis_radarPt_to_img.py webcam --camid 0 --fp16 --fuse
```

prob: The mapped pt only move on the horizontal line on the picture because the mmwave radar pt have not vertical value.

ref: 

https://github.com/goruck/radar-ml

[20221124_ppt](./doc/jorjin_20221124.pptx)

![image](https://raw.githubusercontent.com/goruck/radar-ml/master/images/coord_system.jpg)

