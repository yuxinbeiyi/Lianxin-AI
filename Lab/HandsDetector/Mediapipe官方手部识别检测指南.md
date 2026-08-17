The MediaPipe Hand Landmarker task lets you detect the landmarks of the hands in an image.
These instructions show you how to use the Hand Landmarker with Python. The
code sample described in these instructions is available on
[GitHub](https://github.com/googlesamples/mediapipe/blob/main/examples/hand_landmarker/python/hand_landmarker.ipynb).

For more information about the capabilities, models, and configuration options
of this task, see the [Overview](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker/index).

## Code example

The example code for Hand Landmarker provides a complete implementation of this
task in Python for your reference. This code helps you test this task and get
started on building your own hand landmark detector. You can view, run, and
edit the
[Hand Landmarker example code](https://colab.research.google.com/github/googlesamples/mediapipe/blob/main/examples/hand_landmarker/python/hand_landmarker.ipynb)
using just your web browser.

If you are implementing the Hand Landmarker for Raspberry Pi, refer to the
[Raspberry Pi example app](https://github.com/google-ai-edge/mediapipe-samples/tree/main/examples/hand_landmarker/raspberry_pi).

## Setup

This section describes key steps for setting up your development environment and
code projects specifically to use Hand Landmarker. For general information on
setting up your development environment for using MediaPipe tasks, including
platform version requirements, see the
[Setup guide for Python](https://developers.google.com/mediapipe/solutions/setup_python).

> [!WARNING]
> **Attention:** This MediaPipe Solutions Preview is an early release. [Learn more](https://developers.google.com/edge/mediapipe/solutions/about#notice).

### Packages

The MediaPipe Hand Landmarker task requires the mediapipe PyPI package.
You can install and import these dependencies with the following:

    $ python -m pip install mediapipe

### Imports

Import the following classes to access the Hand Landmarker task functions:

    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision

### Model

The MediaPipe Hand Landmarker task requires a trained model that is compatible with this
task. For more information on available trained models for Hand Landmarker, see
the task overview [Models section](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker/index#models).

Select and download the model, and then store it in a local directory:

    model_path = '/absolute/path/to/gesture_recognizer.task'

Use the `BaseOptions` object `model_asset_path` parameter to specify the path
of the model to use. For a code example, see the next section.

## Create the task

The MediaPipe Hand Landmarker task uses the `create_from_options` function to
set up the task. The `create_from_options` function accepts values
for configuration options to handle. For more information on configuration
options, see [Configuration options](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker/python#configuration_options).

The following code demonstrates how to build and configure this task.

These samples also show the variations of the task construction for images,
video files, and live stream.

### Image

```python
import mediapipe as mp

BaseOptions = mp.tasks.BaseOptions
HandLandmarker = mp.tasks.vision.HandLandmarker
HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode

# Create a hand landmarker instance with the image mode:
options = HandLandmarkerOptions(
    base_options=BaseOptions(model_asset_path='/path/to/model.task'),
    running_mode=VisionRunningMode.IMAGE)
with HandLandmarker.create_from_options(options) as landmarker:
  # The landmarker is initialized. Use it here.
  # ...
    
```

### Video

```python
import mediapipe as mp

BaseOptions = mp.tasks.BaseOptions
HandLandmarker = mp.tasks.vision.HandLandmarker
HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode

# Create a hand landmarker instance with the video mode:
options = HandLandmarkerOptions(
    base_options=BaseOptions(model_asset_path='/path/to/model.task'),
    running_mode=VisionRunningMode.VIDEO)
with HandLandmarker.create_from_options(options) as landmarker:
  # The landmarker is initialized. Use it here.
  # ...
    
```

### Live stream

```python
import mediapipe as mp

BaseOptions = mp.tasks.BaseOptions
HandLandmarker = mp.tasks.vision.HandLandmarker
HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
HandLandmarkerResult = mp.tasks.vision.HandLandmarkerResult
VisionRunningMode = mp.tasks.vision.RunningMode

# Create a hand landmarker instance with the live stream mode:
def print_result(result: HandLandmarkerResult, output_image: mp.Image, timestamp_ms: int):
    print('hand landmarker result: {}'.format(result))

options = HandLandmarkerOptions(
    base_options=BaseOptions(model_asset_path='/path/to/model.task'),
    running_mode=VisionRunningMode.LIVE_STREAM,
    result_callback=print_result)
with HandLandmarker.create_from_options(options) as landmarker:
  # The landmarker is initialized. Use it here.
  # ...
    
```

> [!NOTE]
> **Note:** If you use the video mode or live stream mode, Hand Landmarker uses tracking to avoid triggering palm detection model on every frame, which helps reduce latency.

For a complete example of creating a Hand Landmarker for use with an image, see the
[code example](https://colab.research.google.com/github/googlesamples/mediapipe/blob/main/examples/hand_landmarker/python/hand_landmarker.ipynb#scrollTo=_JVO3rvPD4RN&line=11&uniqifier=1).

### Configuration options

This task has the following configuration options for Python applications:

| Option Name | Description | Value Range | Default Value |
|---|---|---|---|
| `running_mode` | Sets the running mode for the task. There are three modes: <br /> IMAGE: The mode for single image inputs. <br /> VIDEO: The mode for decoded frames of a video. <br /> LIVE_STREAM: The mode for a livestream of input data, such as from a camera. In this mode, resultListener must be called to set up a listener to receive results asynchronously. | {`IMAGE, VIDEO, LIVE_STREAM`} | `IMAGE` |
| `num_hands` | The maximum number of hands detected by the Hand landmark detector. | `Any integer > 0` | `1` |
| `min_hand_detection_confidence` | The minimum confidence score for the hand detection to be considered successful in palm detection model. | `0.0 - 1.0` | `0.5` |
| `min_hand_presence_confidence` | The minimum confidence score for the hand presence score in the hand landmark detection model. In Video mode and Live stream mode, if the hand presence confidence score from the hand landmark model is below this threshold, Hand Landmarker triggers the palm detection model. Otherwise, a lightweight hand tracking algorithm determines the location of the hand(s) for subsequent landmark detections. | `0.0 - 1.0` | `0.5` |
| `min_tracking_confidence` | The minimum confidence score for the hand tracking to be considered successful. This is the bounding box IoU threshold between hands in the current frame and the last frame. In Video mode and Stream mode of Hand Landmarker, if the tracking fails, Hand Landmarker triggers hand detection. Otherwise, it skips the hand detection. | `0.0 - 1.0` | `0.5` |
| `result_callback` | Sets the result listener to receive the detection results asynchronously when the hand landmarker is in live stream mode. Only applicable when running mode is set to `LIVE_STREAM` | N/A | N/A |

## Prepare data

Prepare your input as an image file or a numpy array,
then convert it to a `mediapipe.Image` object. If your input is a video file
or live stream from a webcam, you can use an external library such as
[OpenCV](https://github.com/opencv/opencv) to load your input frames as numpy
arrays.

### Image

```python
import mediapipe as mp

# Load the input image from an image file.
mp_image = mp.Image.create_from_file('/path/to/image')

# Load the input image from a numpy array.
mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=numpy_image)
    
```

### Video

```python
import mediapipe as mp

# Use OpenCV's VideoCapture to load the input video.

# Load the frame rate of the video using OpenCV's CV_CAP_PROP_FPS
# You'll need it to calculate the timestamp for each frame.

# Loop through each frame in the video using VideoCapture#read()

# Convert the frame received from OpenCV to a MediaPipe's Image object.
mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=numpy_frame_from_opencv)
    
```

### Live stream

```python
import mediapipe as mp

# Use OpenCV's VideoCapture to start capturing from the webcam.

# Create a loop to read the latest frame from the camera using VideoCapture#read()

# Convert the frame received from OpenCV to a MediaPipe's Image object.
mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=numpy_frame_from_opencv)
    
```

## Run the task

The Hand Landmarker uses the detect, detect_for_video and detect_async
functions to trigger inferences. For hand landmarks detection, this involves
preprocessing input data, detecting hands in the image and detecting hand
landmarks.

The following code demonstrates how to execute the processing with the task model.

### Image

```python
# Perform hand landmarks detection on the provided single image.
# The hand landmarker must be created with the image mode.
hand_landmarker_result = landmarker.detect(mp_image)
    
```

### Video

```python
# Perform hand landmarks detection on the provided single image.
# The hand landmarker must be created with the video mode.
hand_landmarker_result = landmarker.detect_for_video(mp_image, frame_timestamp_ms)
    
```

### Live stream

```python
# Send live image data to perform hand landmarks detection.
# The results are accessible via the `result_callback` provided in
# the `HandLandmarkerOptions` object.
# The hand landmarker must be created with the live stream mode.
landmarker.detect_async(mp_image, frame_timestamp_ms)
    
```

Note the following:

- When running in the video mode or the live stream mode, you must also provide the Hand Landmarker task the timestamp of the input frame.
- When running in the image or the video model, the Hand Landmarker task will block the current thread until it finishes processing the input image or frame.
- When running in the live stream mode, the Hand Landmarker task doesn't block the current thread but returns immediately. It will invoke its result listener with the detection result every time it has finished processing an input frame. If the detection function is called when the Hand Landmarker task is busy processing another frame, the task will ignore the new input frame.

For a complete example of running an Hand Landmarker on an image, see the
[code example](https://colab.research.google.com/github/googlesamples/mediapipe/blob/main/examples/hand_landmarker/python/hand_landmarker.ipynb#scrollTo=_JVO3rvPD4RN&line=11&uniqifier=1) for details.

## Handle and display results

The Hand Landmarker generates a hand landmarker result object for each detection
run. The result object contains hand landmarks in image coordinates, hand
landmarks in world coordinates and handedness(left/right hand) of the detected
hands.

The following shows an example of the output data from this task:

The `HandLandmarkerResult` output contains three components. Each component is an array, where each element contains the following results for a single detected hand:

- Handedness

  Handedness represents whether the detected hands are left or right hands.
- Landmarks

  There are 21 hand landmarks, each composed of `x`, `y` and `z` coordinates. The
  `x` and `y` coordinates are normalized to \[0.0, 1.0\] by the image width and
  height, respectively. The `z` coordinate represents the landmark depth, with
  the depth at the wrist being the origin. The smaller the value, the closer the
  landmark is to the camera. The magnitude of `z` uses roughly the same scale as
  `x`.
- World Landmarks

  The 21 hand landmarks are also presented in world coordinates. Each landmark
  is composed of `x`, `y`, and `z`, representing real-world 3D coordinates in
  meters with the origin at the hand's geometric center.

    HandLandmarkerResult:
      Handedness:
        Categories #0:
          index        : 0
          score        : 0.98396
          categoryName : Left
      Landmarks:
        Landmark #0:
          x            : 0.638852
          y            : 0.671197
          z            : -3.41E-7
        Landmark #1:
          x            : 0.634599
          y            : 0.536441
          z            : -0.06984
        ... (21 landmarks for a hand)
      WorldLandmarks:
        Landmark #0:
          x            : 0.067485
          y            : 0.031084
          z            : 0.055223
        Landmark #1:
          x            : 0.063209
          y            : -0.00382
          z            : 0.020920
        ... (21 world landmarks for a hand)

The following image shows a visualization of the task output:

![A hand in a thumbs up motion with the skeletal structure of the hand mapped out](https://developers.google.com/static/mediapipe/images/solutions/gesture-recognizer.png)

The Hand Landmarker example code demonstrates how to display the
results returned from the task, see the
[code example](https://colab.research.google.com/github/googlesamples/mediapipe/blob/main/examples/hand_landmarker/python/hand_landmarker.ipynb#scrollTo=_JVO3rvPD4RN&line=11&uniqifier=1)
for details.