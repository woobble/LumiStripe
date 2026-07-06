from typing import Any

class AudioProcessor:
    def __init__(self, config: dict[str, float | int], sample_rate: float = ...) -> None: ...
    def feed_samples(self, samples: Any) -> None: ...
    def frame(self) -> tuple[
        float,
        tuple[float, float, float, float, float, float, float, float],
        bool,
        float,
        int,
    ]: ...
    def features(self) -> tuple[
        float,
        float,
        float,
        float,
        float,
        float,
        bool,
        float,
        tuple[float, float, float, float, float, float, float, float],
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        bool,
        bool,
        bool,
    ]: ...
    def state_copy(self) -> AudioProcessor: ...
    def reset(self) -> None: ...
    def normalization_gain(self) -> float: ...
    def stats(self) -> tuple[int, int, int, float, float]: ...
