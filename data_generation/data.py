"""Synthetic data only. Scenario labels never enter agent evidence."""
import csv
import json
import math
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from .temperature import fahrenheit, simulation_celsius


def write_csv(path, rows):
    if not rows:
        return
    with Path(path).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def generate(config, output):
    rng = random.Random(config["seed"])
    start = date.fromisoformat(config["start_date"])
    tz = ZoneInfo(config["timezone"])
    weather, readings = [], []
    for day_index in range(config["days"]):
        day = start + timedelta(days=day_index)
        temp = {1: 15, 2: 16, 3: 18}[day.month] + 1.8 * math.sin(day_index / 4)
        if day.month == 3 and day.day <= 12:
            temp -= 11  # A fictional local cold spell.
        temp = round(temp + rng.uniform(-0.6, 0.6), 2)
        weather.append({"date": day.isoformat(), "mean_temp_f": fahrenheit(temp), "source": "synthetic"})
        for home in config["homes"]:
            scale = (home["sqft"] / 900) ** 0.6
            daily_jitter = rng.uniform(0.96, 1.04)
            for interval in range(288):
                hour = interval / 12
                local = datetime.combine(day, datetime.min.time(), tz) + timedelta(minutes=interval * 5)
                outdoor = temp + 4 * math.sin((hour - 8) * math.pi / 12)
                heat = max(0, 18 - outdoor) * home["heat_sensitivity_kw"]
                routine = 0.28 * scale
                if 6 <= hour < 9:
                    routine += 0.65 * scale
                if 17 <= hour < 22:
                    routine += 0.95 * scale
                if day.weekday() >= 5 and 10 <= hour < 16:
                    routine += 0.18 * scale
                kw = (routine + heat) * daily_jitter * rng.uniform(0.94, 1.06)
                for anomaly in config["injections"]:
                    if anomaly["home"] != home["id"] or day < date.fromisoformat(anomaly["start"]):
                        continue
                    if anomaly["kind"] == "continuous" or (
                        day.weekday() < 5 and anomaly["start_hour"] <= hour < anomaly["end_hour"]
                    ):
                        kw += anomaly["extra_kw"]
                readings.append({
                    "home_id": home["id"],
                    "timestamp_utc": local.astimezone(timezone.utc).isoformat(),
                    "kwh": round(kw / 12, 6),
                })
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "homes.csv", config["homes"])
    write_csv(output / "plans.csv", config["plans"])
    write_csv(output / "weather.csv", weather)
    write_csv(output / "readings.csv", readings)
    # Evaluation-only truth; never passed to either agent.
    (output / "scenario_truth.json").write_text(json.dumps(config["injections"], indent=2))
    return readings, weather


def generate_neighborhood(config, weather, output):
    """Benchmark-only March summaries, independent of the five billed homes.

    A separate random stream preserves the existing customer readings exactly.
    Each synthetic neighbor shares the locality, period, and electric heating.
    """
    settings = config["neighborhood"]
    rng = random.Random(config["seed"] + 10000)
    rows = []
    march = [w for w in weather if w["date"].startswith("2026-03")]
    for index in range(settings["homes"]):
        sqft = round(settings["min_sqft"] + index * (settings["max_sqft"] - settings["min_sqft"]) / max(1, settings["homes"] - 1))
        scale = (sqft / 900) ** 0.6
        heat_sensitivity = rng.uniform(0.006, 0.018)
        household_variation = rng.uniform(0.85, 1.15)
        kwh = 0.0
        for day in march:
            weekend = date.fromisoformat(day["date"]).weekday() >= 5
            for hour in range(24):
                # Hourly approximation of the same ordinary daily routines.
                outdoor = simulation_celsius(day["mean_temp_f"]) + 4 * math.sin((hour + 0.5 - 8) * math.pi / 12)
                routine = 0.28 * scale
                if 6 <= hour < 9:
                    routine += 0.65 * scale
                if 17 <= hour < 22:
                    routine += 0.95 * scale
                if weekend and 10 <= hour < 16:
                    routine += 0.18 * scale
                kwh += (routine + max(0, 18 - outdoor) * heat_sensitivity) * household_variation
        rows.append({"neighbor_id": f"NB-{index + 1:03}", "sqft": sqft, "locality": config["locality"],
                     "heating": "electric", "month": "2026-03", "kwh": round(kwh, 3), "source": "synthetic benchmark"})
    write_csv(Path(output) / "neighborhood.csv", rows)
    return rows
