"""Optional TFLite keypoint classifier adapted from the reference project.

The classifier is deliberately independent from the existing rule classifier.
If TensorFlow/TFLite is unavailable or the model is missing, it stays disabled
and the rest of HandsDetector continues to work normally.
"""

import csv
import copy
import itertools
from pathlib import Path
from typing import Optional

import numpy as np


class TFLiteKeypointClassifier:
    def __init__(self, model_path: Path, labels_path: Path,
                 num_threads: int = 1):
        self.model_path = Path(model_path)
        self.labels_path = Path(labels_path)
        self._interpreter = None
        self._input_index = None
        self._output_index = None
        self.labels = self._load_labels()
        self.error: Optional[str] = None
        self.enabled = self._load_interpreter(num_threads)

    def _load_labels(self):
        if not self.labels_path.exists():
            return []
        with self.labels_path.open("r", encoding="utf-8-sig", newline="") as f:
            return [row[0].strip() for row in csv.reader(f) if row and row[0].strip()]

    def _load_interpreter(self, num_threads: int) -> bool:
        if not self.model_path.exists():
            self.error = f"model missing: {self.model_path}"
            return False
        try:
            try:
                from tflite_runtime.interpreter import Interpreter
            except ImportError:
                from tensorflow.lite import Interpreter
            self._interpreter = Interpreter(model_path=str(self.model_path),
                                            num_threads=num_threads)
            self._interpreter.allocate_tensors()
            inputs = self._interpreter.get_input_details()
            outputs = self._interpreter.get_output_details()
            self._input_index = inputs[0]["index"]
            self._output_index = outputs[0]["index"]
            return True
        except Exception as exc:
            self.error = str(exc)
            self._interpreter = None
            return False

    @staticmethod
    def preprocess(landmarks) -> list[float]:
        points = [[float(point.x), float(point.y)] for point in landmarks]
        if len(points) != 21:
            raise ValueError("expected 21 hand landmarks")
        points = copy.deepcopy(points)
        base_x, base_y = points[0]
        for point in points:
            point[0] -= base_x
            point[1] -= base_y
        values = list(itertools.chain.from_iterable(points))
        max_value = max(map(abs, values), default=0.0)
        if max_value == 0:
            return [0.0] * 42
        return [value / max_value for value in values]

    def predict(self, landmarks):
        if not self.enabled or landmarks is None:
            return "NONE", 0.0
        try:
            values = np.asarray([self.preprocess(landmarks)], dtype=np.float32)
            self._interpreter.set_tensor(self._input_index, values)
            self._interpreter.invoke()
            scores = np.squeeze(self._interpreter.get_tensor(self._output_index))
            scores = np.asarray(scores, dtype=np.float32)
            index = int(np.argmax(scores))
            confidence = float(scores[index])
            label = self.labels[index] if index < len(self.labels) else str(index)
            return label, confidence
        except Exception as exc:
            self.error = str(exc)
            return "NONE", 0.0
