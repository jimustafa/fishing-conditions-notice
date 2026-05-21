import base64
import io
import math
import os
import smtplib
import subprocess
import sys
import tempfile
import webbrowser
from datetime import datetime, timedelta, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import ephem
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import requests
import typer
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader
from timezonefinder import TimezoneFinder

load_dotenv(Path(__file__).parent.parent / ".env")

BASE_URL = "https://ocean-systems.uc.r.appspot.com/api"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
DEFAULT_CHECK_HOURS = 4
DEFAULT_PLOT_DAYS = 7

_jinja = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)

plt.style.use(Path(__file__).parent.parent / "mplstyle")

app = typer.Typer()


def c_to_f(celsius: float) -> float:
    return celsius * 9 / 5 + 32


def geocode(location: str) -> tuple[float, float]:
    _, contact_email = parseaddr(os.environ["EMAIL_FROM"])
    user_agent = f"fishing-conditions-notice/1.0 ({contact_email})"
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": location, "format": "json", "limit": 1},
        headers={"User-Agent": user_agent},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        typer.echo(f"Location not found: {location!r}", err=True)
        raise typer.Exit(1)
    return float(results[0]["lat"]), float(results[0]["lon"])


def get_timezone(lat: float, lon: float) -> str:
    return TimezoneFinder().timezone_at(lat=lat, lng=lon) or "UTC"


def fetch_spotter_data(site_id: int, days: int) -> pd.Series:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    resp = requests.get(
        f"{BASE_URL}/sites/{site_id}/spotter_data",
        params={
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
        },
        timeout=15,
    )
    resp.raise_for_status()
    records = resp.json().get("topTemperature", [])
    if not records:
        return pd.Series(dtype=float)
    series = pd.Series(
        {
            datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")): c_to_f(
                record["value"]
            )
            for record in records
        }
    ).sort_index()
    return series


def fetch_latest_data(site_id: int) -> dict | None:
    resp = requests.get(f"{BASE_URL}/sites/{site_id}/latest_data", timeout=15)
    resp.raise_for_status()
    records = resp.json().get("latestData", [])
    by_metric = {record["metric"]: record for record in records}

    wind_wave_metrics = [
        "wind_speed",
        "wind_direction",
        "significant_wave_height",
        "wave_mean_period",
        "wave_mean_direction",
    ]
    if not any(metric in by_metric for metric in wind_wave_metrics):
        return None

    first_metric = next(m for m in wind_wave_metrics if m in by_metric)
    as_of = datetime.fromisoformat(by_metric[first_metric]["timestamp"].replace("Z", "+00:00"))
    wind_speed_ms = by_metric.get("wind_speed", {}).get("value")
    return {
        "wind_speed": wind_speed_ms * 2.23694 if wind_speed_ms is not None else None,
        "wind_direction": by_metric.get("wind_direction", {}).get("value"),
        "wave_height": by_metric.get("significant_wave_height", {}).get("value"),
        "wave_period": by_metric.get("wave_mean_period", {}).get("value"),
        "wave_direction": by_metric.get("wave_mean_direction", {}).get("value"),
        "as_of": as_of,
    }


def _moon_phase_name(illumination: float, waxing: bool) -> str:
    if illumination < 2:
        return "New Moon"
    if illumination > 98:
        return "Full Moon"
    if abs(illumination - 50) < 8:
        return "First Quarter" if waxing else "Last Quarter"
    if illumination < 50:
        return "Waxing Crescent" if waxing else "Waning Crescent"
    return "Waxing Gibbous" if waxing else "Waning Gibbous"


def fetch_moon_data(lat: float, lon: float, timezone_name: str) -> dict:
    timezone_info = ZoneInfo(timezone_name)
    today_local = datetime.now(timezone_info).replace(hour=0, minute=0, second=0, microsecond=0)

    observer = ephem.Observer()
    observer.lat = str(lat)
    observer.lon = str(lon)
    observer.elevation = 0
    observer.pressure = 0

    moon = ephem.Moon()

    # Illumination at noon local
    noon_utc = today_local.replace(hour=12).astimezone(timezone.utc)
    observer.date = noon_utc.strftime("%Y/%m/%d %H:%M:%S")
    moon.compute(observer)
    illumination = float(moon.phase)

    # Waxing vs waning: compare to yesterday noon
    yesterday_utc = (today_local.replace(hour=12) - timedelta(days=1)).astimezone(timezone.utc)
    observer.date = yesterday_utc.strftime("%Y/%m/%d %H:%M:%S")
    moon_yesterday = ephem.Moon()
    moon_yesterday.compute(observer)
    waxing = illumination > float(moon_yesterday.phase)

    # Rise and set from midnight local
    midnight_utc = today_local.astimezone(timezone.utc)
    observer.date = midnight_utc.strftime("%Y/%m/%d %H:%M:%S")
    moon.compute(observer)

    rise_str = None
    set_str = None

    try:
        rise_utc = (
            ephem.Date(observer.next_rising(moon)).datetime().replace(tzinfo=timezone.utc)
        )
        rise_local = rise_utc.astimezone(timezone_info)
        if rise_local.date() == today_local.date():
            rise_str = rise_local.strftime("%-I:%M %p")
    except (ephem.NeverUpError, ephem.AlwaysUpError):
        pass

    try:
        set_utc = (
            ephem.Date(observer.next_setting(moon)).datetime().replace(tzinfo=timezone.utc)
        )
        set_local = set_utc.astimezone(timezone_info)
        if set_local.date() == today_local.date():
            set_str = set_local.strftime("%-I:%M %p")
    except (ephem.NeverUpError, ephem.AlwaysUpError):
        pass

    # Altitude at 9 PM local
    nine_pm_utc = today_local.replace(hour=21).astimezone(timezone.utc)
    observer.date = nine_pm_utc.strftime("%Y/%m/%d %H:%M:%S")
    moon.compute(observer)
    altitude_deg = round(math.degrees(float(moon.alt)), 1)

    return {
        "phase_name": _moon_phase_name(illumination, waxing),
        "illumination": round(illumination),
        "rise": rise_str,
        "set": set_str,
        "altitude_at_9pm": altitude_deg,
    }


def degree_to_cardinal(degree: float) -> str:
    dirs = [
        "N",
        "NNE",
        "NE",
        "ENE",
        "E",
        "ESE",
        "SE",
        "SSE",
        "S",
        "SSW",
        "SW",
        "WSW",
        "W",
        "WNW",
        "NW",
        "NNW",
    ]
    return dirs[round(degree / 22.5) % 16]


def time_weighted_avg(series: pd.Series) -> float:
    """Average weighted by duration each reading represents."""
    times = series.index.to_series()
    deltas = times.diff().dt.total_seconds().fillna(0)
    deltas.iloc[0] = deltas.iloc[1] if len(deltas) > 1 else 1
    return float((series * deltas).sum() / deltas.sum())


def make_plot(
    series: pd.Series,
    site_id: int,
    threshold_f: float | None,
    plot_days: int,
) -> str:
    fig, ax = plt.subplots()
    ax.plot(series.index, series, color="steelblue")
    if threshold_f is not None:
        ax.axhline(
            threshold_f,
            color="firebrick",
            linestyle="--",
            label=f"Temperature threshold {threshold_f}°F",
        )
    xlim_end = series.index[-1].ceil("D")
    xlim_start = xlim_end - timedelta(days=plot_days + 1)
    ax.set_xlim(xlim_start, xlim_end)
    day = xlim_start.ceil("D")
    band = True
    while day < xlim_end:
        next_day = day + timedelta(days=1)
        if band:
            ax.axvspan(day, next_day, color="grey", alpha=0.1, linewidth=0)
        day = next_day
        band = not band
    ax.set_title(f"Aqualink Site {site_id}\nWater Temperature (last {plot_days} days)")
    ax.set_ylabel("Water Temperature (°F)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_major_locator(mdates.DayLocator())
    ax.xaxis.set_minor_locator(mdates.HourLocator(byhour=range(0, 24, 4)))
    ax.yaxis.set_minor_locator(plt.MultipleLocator(1))
    fig.autofmt_xdate()
    ax.grid(True, which="major")
    ax.grid(True, which="minor", linewidth=0.5, alpha=0.5)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def _git_hash() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=Path(__file__).parent.parent,
        text=True,
    ).strip()


def render_html(
    site_id: int,
    avg_temperature: float,
    latest_temperature: float,
    water_temperature_threshold: float | None,
    check_hours: int,
    reading_count: int,
    timezone_name: str,
    latest_data: dict[str, float | None] | None,
    moon_data: dict | None,
    plot_b64: str,
    plot_src: str | None = None,
) -> str:
    timezone_info = ZoneInfo(timezone_name)
    if latest_data is not None:
        wind_wave = {
            "wind_speed": latest_data["wind_speed"],
            "wind_cardinal": None,
            "wave_height": latest_data.get("wave_height"),
            "wave_period": latest_data.get("wave_period"),
            "wave_cardinal": None,
            "as_of": None,
        }
        if wind_direction := latest_data.get("wind_direction"):
            wind_wave["wind_cardinal"] = degree_to_cardinal(wind_direction)
        if wave_direction := latest_data.get("wave_direction"):
            wind_wave["wave_cardinal"] = degree_to_cardinal(wave_direction)
        if as_of := latest_data.get("as_of"):
            wind_wave["as_of"] = as_of.astimezone(timezone_info).strftime("%Y-%m-%d %H:%M %Z")
    else:
        wind_wave = None
    context = {
        "site_id": site_id,
        "avg_temperature": avg_temperature,
        "latest_temperature": latest_temperature,
        "threshold_temperature": water_temperature_threshold,
        "check_hours": check_hours,
        "reading_count": reading_count,
        "wind_wave": wind_wave,
        "moon": moon_data,
        "plot_src": plot_src or f"data:image/png;base64,{plot_b64}",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "generated_at_local": datetime.now(timezone_info).strftime("%Y-%m-%d %H:%M %Z"),
        "git_hash": _git_hash(),
    }
    return _jinja.get_template("fishing-conditions-notice.html").render(**context)


def send_notice(
    site_id: int,
    location: str,
    avg_temperature: float,
    latest_temperature: float,
    water_temperature_threshold: float | None,
    check_hours: int,
    reading_count: int,
    timezone_name: str,
    email_to: str,
    latest_data: dict[str, float | None] | None,
    moon_data: dict | None,
    plot_b64: str,
) -> None:
    email_from = os.environ["EMAIL_FROM"]
    smtp_user = os.environ["SMTP_USER"]
    smtp_password = os.environ["SMTP_PASSWORD"]
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    html = render_html(
        site_id,
        avg_temperature,
        latest_temperature,
        water_temperature_threshold,
        check_hours,
        reading_count,
        timezone_name,
        latest_data,
        moon_data,
        plot_b64,
        plot_src="cid:plot",
    )
    msg = MIMEMultipart("related")
    msg["Subject"] = f"Fishing Conditions — {location} · {avg_temperature:.1f}°F"
    msg["From"] = email_from
    msg["To"] = ", ".join(addr.strip() for addr in email_to.split(","))
    msg.attach(MIMEText(html, "html"))

    img = MIMEImage(base64.b64decode(plot_b64), "png")
    img.add_header("Content-ID", "<plot>")
    img.add_header("Content-Disposition", "inline", filename="water-temperature.png")
    msg.attach(img)

    with smtplib.SMTP(smtp_host, smtp_port) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(msg)


def _dev(
    site_id: int,
    location: str,
    water_temperature_threshold: float | None,
    check_hours: int,
    plot_days: int,
    html_out: Path | None,
) -> None:
    from livereload import Server

    out = html_out or Path("build/fishing-conditions-notice_preview.html")
    out.parent.mkdir(parents=True, exist_ok=True)

    def rebuild():
        lat, lon = geocode(location)
        timezone_name = get_timezone(lat, lon)
        series = fetch_spotter_data(site_id, plot_days)
        if series.empty:
            print(f"No spotter data for site {site_id}")
            return
        cutoff = datetime.now(timezone.utc) - timedelta(hours=check_hours)
        recent = series[series.index >= cutoff]
        if recent.empty:
            print(f"No readings in last {check_hours}h")
            return
        avg_temperature = time_weighted_avg(recent)
        latest_temperature = float(recent.iloc[-1])
        reading_count = len(recent)
        latest_data = fetch_latest_data(site_id)
        moon_data = fetch_moon_data(lat, lon, timezone_name)
        timezone_info = ZoneInfo(timezone_name)
        plot_b64 = make_plot(
            series.set_axis(series.index.tz_convert(timezone_info).tz_localize(None)),
            site_id,
            water_temperature_threshold,
            plot_days,
        )
        html = render_html(
            site_id,
            avg_temperature,
            latest_temperature,
            water_temperature_threshold,
            check_hours,
            reading_count,
            timezone_name,
            latest_data,
            moon_data,
            plot_b64,
        )
        out.write_text(html)
        print(f"Rebuilt → {out}")

    server = Server()
    server.watch(
        str(Path(__file__)), lambda: os.execv(sys.executable, [sys.executable] + sys.argv)
    )
    server.watch(str(TEMPLATES_DIR / "fishing-conditions-notice.html"), rebuild)
    rebuild()
    server.serve(root=str(out.parent), default_filename=out.name)


@app.command()
def main(
    location: Annotated[str, typer.Option(envvar="LOCATION")],
    site_id: Annotated[int, typer.Option(envvar="AQUALINK_SITE_ID")],
    water_temperature_threshold: Annotated[
        float | None, typer.Option("--water-temperature-threshold")
    ] = None,
    check_hours: Annotated[int, typer.Option()] = DEFAULT_CHECK_HOURS,
    plot_days: Annotated[int, typer.Option()] = DEFAULT_PLOT_DAYS,
    email_to: Annotated[str | None, typer.Option(envvar="EMAIL_TO")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    html_out: Annotated[Path | None, typer.Option("--html-out")] = None,
    dev: Annotated[bool, typer.Option("--dev")] = False,
) -> None:
    if not os.environ.get("EMAIL_FROM"):
        typer.echo("Error: EMAIL_FROM not set", err=True)
        raise typer.Exit(1)

    if dev:
        _dev(
            site_id,
            location,
            water_temperature_threshold,
            check_hours,
            plot_days,
            html_out,
        )
        return

    lat, lon = geocode(location)
    timezone_name = get_timezone(lat, lon)
    series = fetch_spotter_data(site_id, plot_days)
    if series.empty:
        print(f"No spotter data for site {site_id}")
        return

    cutoff = datetime.now(timezone.utc) - timedelta(hours=check_hours)
    recent = series[series.index >= cutoff]

    if recent.empty:
        print(f"No readings in last {check_hours}h")
        return

    avg_temperature = time_weighted_avg(recent)
    latest_temperature = float(recent.iloc[-1])
    reading_count = len(recent)
    latest_data = fetch_latest_data(site_id)
    moon_data = fetch_moon_data(lat, lon, timezone_name)

    print(
        f"Site {site_id}: {reading_count} readings in last {check_hours}h, "
        f"time-weighted avg {avg_temperature:.1f}°F, latest {latest_temperature:.1f}°F"
    )

    below_threshold = (
        water_temperature_threshold is not None
        and avg_temperature <= water_temperature_threshold
    )

    timezone_info = ZoneInfo(timezone_name)

    if dry_run:
        plot_b64 = make_plot(
            series.set_axis(series.index.tz_convert(timezone_info).tz_localize(None)),
            site_id,
            water_temperature_threshold,
            plot_days,
        )
        html = render_html(
            site_id,
            avg_temperature,
            latest_temperature,
            water_temperature_threshold,
            check_hours,
            reading_count,
            timezone_name,
            latest_data,
            moon_data,
            plot_b64,
        )
        if html_out:
            html_out.write_text(html)
            print(f"HTML written to {html_out}")
        else:
            with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as tf:
                tf.write(html)
                webbrowser.open(f"file://{tf.name}")
            print(f"Preview opened: {tf.name}")
        if below_threshold:
            print(f"Below threshold ({water_temperature_threshold}°F) — would not send")
        else:
            print("Dry run — email not sent")
        return

    if below_threshold:
        print(f"Below threshold ({water_temperature_threshold}°F) — no notice sent")
        return

    reason = (
        f"above threshold ({water_temperature_threshold}°F)"
        if water_temperature_threshold is not None
        else "no threshold set"
    )
    print(f"Sending notice — {reason}")
    plot_b64 = make_plot(
        series.set_axis(series.index.tz_convert(timezone_info).tz_localize(None)),
        site_id,
        water_temperature_threshold,
        plot_days,
    )

    if email_to is None:
        typer.echo("Error: EMAIL_TO not set", err=True)
        raise typer.Exit(1)

    send_notice(
        site_id,
        location,
        avg_temperature,
        latest_temperature,
        water_temperature_threshold,
        check_hours,
        reading_count,
        timezone_name,
        email_to,
        latest_data,
        moon_data,
        plot_b64,
    )
    print(f"Notice sent: {avg_temperature:.1f}°F")


if __name__ == "__main__":
    app()
