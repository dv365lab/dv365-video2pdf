import os
import time
import cv2
import imutils
import shutil
import img2pdf
import glob
import argparse
from pathlib import Path
import numpy as np
import zxingcpp

############# Define constants

OUTPUT_SLIDES_DIR = f"./output"

FRAME_RATE = 0.5                 # no.of frames per second that needs to be processed, fewer the count faster the speed
WARMUP = FRAME_RATE              # initial number of frames to be skipped
FGBG_HISTORY = max(1, round(FRAME_RATE * 15))   # no.of frames in background object
VAR_THRESHOLD = 16               # Threshold on the squared Mahalanobis distance between the pixel and the model to decide whether a pixel is well described by the background model.
DETECT_SHADOWS = False            # If true, the algorithm will detect shadows and mark them.
MIN_PERCENT = 0.1                # min % of diff between foreground and background to detect if motion has stopped
MAX_PERCENT = 3                  # max % of diff between foreground and background to detect if frame is still in motion


def get_frames(video_path):
    '''A fucntion to return the frames from a video located at video_path
    this function skips frames as defined in FRAME_RATE'''
    
    
    # open a pointer to the video file initialize the width and height of the frame
    vs = cv2.VideoCapture(video_path)
    if not vs.isOpened():
        raise Exception(f'unable to open file {video_path}')


    total_frames = vs.get(cv2.CAP_PROP_FRAME_COUNT)
    fps = vs.get(cv2.CAP_PROP_FPS)
    duration = total_frames / fps if total_frames > 0 and fps > 0 else None
    frame_time = 0
    frame_count = 0
    print("total_frames: ", total_frames)
    print("FRAME_RATE", FRAME_RATE)

    # loop over the frames of the video
    # Some decoders keep returning the last frame after an out-of-range seek.
    while duration is None or frame_time < duration:
        # grab a frame from the video

        vs.set(cv2.CAP_PROP_POS_MSEC, frame_time * 1000)    # move frame to a timestamp
        frame_time += 1/FRAME_RATE

        (success, frame) = vs.read()
        # if the frame is None, then we have reached the end of the video file
        if not success or frame is None:
            break

        frame_count += 1
        yield frame_count, frame_time, frame

    vs.release()
 


def crop_frame(frame, crop_top=0, crop_bottom=0):
    '''Remove header and footer rows without modifying the source frame.'''
    height = frame.shape[0]
    if crop_top < 0 or crop_bottom < 0:
        raise ValueError('crop sizes must be non-negative')
    if crop_top + crop_bottom >= height:
        raise ValueError(f'crop sizes must add up to less than the frame height ({height})')
    return frame[crop_top:height - crop_bottom, :]


def remove_qr_codes(frame):
    '''Cover detected QR polygons using nearby background colors.'''
    codes = zxingcpp.read_barcodes(frame, formats=zxingcpp.BarcodeFormat.QRCode)
    if not codes:
        return frame

    cleaned = frame.copy()
    kernel = np.ones((9, 9), dtype=np.uint8)
    for code in codes:
        position = code.position
        corners = [position.top_left, position.top_right, position.bottom_right, position.bottom_left]
        polygon = np.array([(point.x, point.y) for point in corners], dtype=np.int32)
        mask = np.zeros(frame.shape[:2], dtype=np.uint8)
        cv2.fillConvexPoly(mask, polygon, 255)
        mask = cv2.dilate(mask, kernel)  # Include four pixels around the detected code.
        ring = (cv2.dilate(mask, kernel) != 0) & (mask == 0)
        pixels = frame[ring] if np.any(ring) else frame[mask != 0]
        # Favor the lighter background over nearby text and slide borders.
        background = np.percentile(pixels, 75, axis=0).astype(frame.dtype)
        cleaned[mask != 0] = background
    return cleaned


def detect_unique_screenshots(video_path, output_folder_screenshot_path, crop_top=0, crop_bottom=0, remove_qr=False):
    ''''''
    # Initialize fgbg a Background object with Parameters
    # history = The number of frames history that effects the background subtractor
    # varThreshold = Threshold on the squared Mahalanobis distance between the pixel and the model to decide whether a pixel is well described by the background model. This parameter does not affect the background update.
    # detectShadows = If true, the algorithm will detect shadows and mark them. It decreases the speed a bit, so if you do not need this feature, set the parameter to false.

    fgbg = cv2.createBackgroundSubtractorMOG2(history=FGBG_HISTORY, varThreshold=VAR_THRESHOLD,detectShadows=DETECT_SHADOWS)

    
    captured = False
    start_time = time.time()
    (W, H) = (None, None)

    screenshoots_count = 0
    for frame_count, frame_time, frame in get_frames(video_path):
        if remove_qr:
            frame = remove_qr_codes(frame)
        frame = crop_frame(frame, crop_top, crop_bottom)
        orig = frame.copy() # clone the original frame (so we can save it later), 
        frame = imutils.resize(frame, width=600) # resize the frame
        mask = fgbg.apply(frame) # apply the background subtractor

        # apply a series of erosions and dilations to eliminate noise
#            eroded_mask = cv2.erode(mask, None, iterations=2)
#            mask = cv2.dilate(mask, None, iterations=2)

        # if the width and height are empty, grab the spatial dimensions
        if W is None or H is None:
            (H, W) = mask.shape[:2]

        # compute the percentage of the mask that is "foreground"
        p_diff = (cv2.countNonZero(mask) / float(W * H)) * 100

        # if p_diff less than N% then motion has stopped, thus capture the frame

        if p_diff < MIN_PERCENT and not captured and frame_count > WARMUP:
            captured = True
            filename = f"{screenshoots_count:03}_{round(frame_time/60, 2)}.png"

            path = os.path.join(output_folder_screenshot_path, filename)
            print("saving {}".format(path))
            cv2.imwrite(path, orig)
            screenshoots_count += 1

        # otherwise, either the scene is changing or we're still in warmup
        # mode so let's wait until the scene has settled or we're finished
        # building the background model
        elif captured and p_diff >= MAX_PERCENT:
            captured = False
    print(f'{screenshoots_count} screenshots Captured!')
    print(f'Time taken {time.time()-start_time}s')
    return 


def initialize_output_folder(video_path, crop_top=0, crop_bottom=0, remove_qr=False):
    '''Clean the output folder if already exists'''
    video_name = Path(video_path.replace("\\", "/")).stem
    if crop_top or crop_bottom:
        video_name += f"_crop_t{crop_top}_b{crop_bottom}"
    if remove_qr:
        video_name += "_noqr"
    output_folder_screenshot_path = os.path.join(OUTPUT_SLIDES_DIR, video_name)

    if os.path.exists(output_folder_screenshot_path):
        shutil.rmtree(output_folder_screenshot_path)

    os.makedirs(output_folder_screenshot_path, exist_ok=True)
    print('initialized output folder', output_folder_screenshot_path)
    return output_folder_screenshot_path


def convert_screenshots_to_pdf(output_folder_screenshot_path):
    output_pdf_path = str(output_folder_screenshot_path) + '.pdf'
    print('output_folder_screenshot_path', output_folder_screenshot_path)
    print('output_pdf_path', output_pdf_path)
    print('converting images to pdf..')
    with open(output_pdf_path, "wb") as f:
        f.write(img2pdf.convert(sorted(glob.glob(f"{output_folder_screenshot_path}/*.png"))))
    print('Pdf Created!')
    print('pdf saved at', output_pdf_path)


if __name__ == "__main__":
    
#     video_path = "./input/Test Video 2.mp4"
#     choice = 'y'
#     output_folder_screenshot_path = initialize_output_folder(video_path)
    
    
    parser = argparse.ArgumentParser("video_path")
    parser.add_argument("video_path", help="path of video to be converted to pdf slides", type=str)
    parser.add_argument("--review", action="store_true", help="pause to review screenshots before creating the PDF")
    parser.add_argument("--crop-top", type=int, default=0, metavar="PIXELS", help="crop this many pixels from the top before detection and saving")
    parser.add_argument("--crop-bottom", type=int, default=0, metavar="PIXELS", help="crop this many pixels from the bottom before detection and saving")
    parser.add_argument("--remove-qr", action="store_true", help="detect QR codes and cover them with nearby background colors")
    args = parser.parse_args()
    video_path = args.video_path

    if args.crop_top or args.crop_bottom:
        preview_video = cv2.VideoCapture(video_path)
        try:
            success, preview_frame = preview_video.read()
        finally:
            preview_video.release()
        if not success or preview_frame is None:
            parser.error(f'unable to open file {video_path}')
        try:
            crop_frame(preview_frame, args.crop_top, args.crop_bottom)
        except ValueError as error:
            parser.error(str(error))
        print(f'cropping top {args.crop_top}px and bottom {args.crop_bottom}px')

    print('video_path', video_path)
    output_folder_screenshot_path = initialize_output_folder(video_path, args.crop_top, args.crop_bottom, args.remove_qr)
    detect_unique_screenshots(video_path, output_folder_screenshot_path, args.crop_top, args.crop_bottom, args.remove_qr)

    choice = 'y'
    if args.review:
        print('Please Manually verify screenshots and delete duplicates')
        while True:
            choice = input("Press y to continue and n to terminate")
            choice = choice.lower().strip()
            if choice in ['y', 'n']:
                break
            else:
                print('please enter a valid choice')

    if choice == 'y':
        convert_screenshots_to_pdf(output_folder_screenshot_path)
