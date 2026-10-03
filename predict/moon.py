"""Moon illumination without an ephemeris dependency.

Illuminated fraction from the mean synodic month counted from a reference new moon
(2000-01-06 18:14 UTC): k = (1 − cos(2π · age / P)) / 2. The mean-month approximation is good to
a few percent of illumination, which is ample for a feature that only separates dark nights
from moonlit ones.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SYNODIC_DAYS = 29.530588853
NEW_MOON_2000 = pd.Timestamp("2000-01-06 18:14", tz="UTC")


def illumination(ts) -> np.ndarray:
    t = pd.to_datetime(pd.Series(ts), utc=True)
    age = ((t - NEW_MOON_2000).dt.total_seconds() / 86400) % SYNODIC_DAYS
    return ((1 - np.cos(2 * np.pi * age / SYNODIC_DAYS)) / 2).to_numpy()
