import numpy as np
import pandas as pd


FEATURE_COLUMNS = [
    "return_1",
    "return_3",
    "return_12",
    "volatility_12",
    "range_pct",
    "volume_zscore",
    "trend_10_30",
]


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -30.0, 30.0)))


def _fit_logistic(features: np.ndarray, labels: np.ndarray, l2: float) -> tuple[np.ndarray, float]:
    weights = np.zeros(features.shape[1], dtype=float)
    bias = 0.0
    learning_rate = 0.08
    for _ in range(350):
        probabilities = _sigmoid(features @ weights + bias)
        error = probabilities - labels
        weights -= learning_rate * ((features.T @ error) / len(labels) + l2 * weights)
        bias -= learning_rate * float(error.mean())
    return weights, bias


def _metrics(probabilities: np.ndarray, labels: np.ndarray) -> dict:
    predictions = probabilities >= 0.5
    positives = predictions.sum()
    true_positives = np.logical_and(predictions, labels == 1).sum()
    return {
        "accuracy": round(float((predictions == labels).mean()), 4),
        "precision": round(float(true_positives / positives), 4) if positives else 0.0,
        "brier": round(float(np.mean((probabilities - labels) ** 2)), 4),
        "samples": int(len(labels)),
    }


def _feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)
    log_return = np.log(close.where(close > 0)).diff()
    volume_mean = volume.rolling(24).mean()
    volume_std = volume.rolling(24).std().replace(0, np.nan)
    features = pd.DataFrame(index=df.index)
    features["return_1"] = log_return
    features["return_3"] = log_return.rolling(3).sum()
    features["return_12"] = log_return.rolling(12).sum()
    features["volatility_12"] = log_return.rolling(12).std()
    features["range_pct"] = (high - low) / close.replace(0, np.nan)
    features["volume_zscore"] = (volume - volume_mean) / volume_std
    features["trend_10_30"] = close.rolling(10).mean() / close.rolling(30).mean() - 1
    return features.replace([np.inf, -np.inf], np.nan)


def supervised_market_prediction(
    df: pd.DataFrame,
    horizon: int = 3,
    minimum_edge: float = 0.0025,
) -> dict:
    """Fit a purged chronological model and predict the next market direction."""
    if len(df) < 180 or not {"open", "high", "low", "close", "volume"}.issubset(df.columns):
        return {"available": False, "reason": "insufficient_history"}

    features = _feature_frame(df)
    future_return = df["close"].astype(float).shift(-horizon) / df["close"].astype(float) - 1
    training = features.copy()
    training["label"] = (future_return > minimum_edge).astype(float)
    training.loc[future_return.isna(), "label"] = np.nan
    training = training.dropna()
    if len(training) < 120 or training["label"].nunique() < 2:
        return {"available": False, "reason": "insufficient_training_variation"}

    sample_count = len(training)
    train_end = int(sample_count * 0.60)
    validation_start = train_end + horizon
    validation_end = int(sample_count * 0.80)
    test_start = validation_end + horizon
    if validation_start >= validation_end or test_start >= sample_count:
        return {"available": False, "reason": "insufficient_purged_splits"}

    x = training[FEATURE_COLUMNS].to_numpy(dtype=float)
    y = training["label"].to_numpy(dtype=float)
    mean = x[:train_end].mean(axis=0)
    std = x[:train_end].std(axis=0)
    std[std < 1e-9] = 1.0
    x = np.clip((x - mean) / std, -8.0, 8.0)

    validation_slice = slice(validation_start, validation_end)
    test_slice = slice(test_start, sample_count)
    candidates: list[tuple[float, float, np.ndarray, float]] = []
    for l2 in (0.01, 0.05, 0.2, 0.8):
        weights, bias = _fit_logistic(x[:train_end], y[:train_end], l2)
        probabilities = _sigmoid(x[validation_slice] @ weights + bias)
        brier = float(np.mean((probabilities - y[validation_slice]) ** 2))
        candidates.append((brier, l2, weights, bias))
    _, selected_l2, weights, bias = min(candidates, key=lambda row: row[0])

    validation_probabilities = _sigmoid(x[validation_slice] @ weights + bias)
    test_probabilities = _sigmoid(x[test_slice] @ weights + bias)
    validation_metrics = _metrics(validation_probabilities, y[validation_slice])
    test_metrics = _metrics(test_probabilities, y[test_slice])

    latest = features.iloc[-1][FEATURE_COLUMNS].to_numpy(dtype=float)
    if not np.isfinite(latest).all():
        return {"available": False, "reason": "latest_features_unavailable"}
    latest_scaled = np.clip((latest - mean) / std, -8.0, 8.0)
    probability = float(_sigmoid(np.asarray([latest_scaled @ weights + bias]))[0])
    reliable = bool(
        test_metrics["samples"] >= 20
        and test_metrics["accuracy"] >= 0.52
        and test_metrics["brier"] <= 0.26
    )
    stance = "bullish" if probability >= 0.58 else ("bearish" if probability <= 0.42 else "neutral")
    score = max(-1.0, min(1.0, (probability - 0.5) * 2)) if reliable else 0.0
    return {
        "available": True,
        "reliable": reliable,
        "probability_up": round(probability, 4),
        "stance": stance if reliable else "unvalidated",
        "score": round(score, 4),
        "horizon_bars": horizon,
        "minimum_edge_pct": round(minimum_edge * 100, 4),
        "split": {"train_pct": 60, "validation_pct": 20, "test_pct": 20, "purge_bars": horizon},
        "regularization": {"type": "l2", "strength": selected_l2},
        "validation": validation_metrics,
        "test": test_metrics,
    }
