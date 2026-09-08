from enum import Enum


class PetState(str, Enum):
    IDLE = "idle"
    SIT = "sit"
    SLEEP = "sleep"
    WALK_LEFT = "walk_left"
    WALK_RIGHT = "walk_right"
