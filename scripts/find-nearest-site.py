"""Find nearest Aqualink sites to a given location."""

import argparse
import json
import math
import os
import sys
from email.utils import parseaddr

import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()

BASE_URL = "https://ocean-systems.uc.r.appspot.com/api"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_miles = 3958.8
    lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    hav = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    return earth_radius_miles * 2 * math.atan2(math.sqrt(hav), math.sqrt(1 - hav))


def geocode(location: str, user_agent: str) -> tuple[float, float]:
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": location, "format": "json", "limit": 1},
        headers={"User-Agent": user_agent},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        print(f"Location not found: {location!r}", file=sys.stderr)
        sys.exit(1)
    return float(results[0]["lat"]), float(results[0]["lon"])


def fetch_sites(with_spotter: bool) -> list[dict]:
    resp = requests.get(f"{BASE_URL}/sites", timeout=30)
    resp.raise_for_status()
    sites = resp.json()
    sites = [s for s in sites if s.get("display") and s.get("polygon")]
    if with_spotter:
        sites = [s for s in sites if s.get("sensorId")]
    return sites


def main() -> None:
    parser = argparse.ArgumentParser(description="Find nearest Aqualink sites to a location")
    loc = parser.add_mutually_exclusive_group(required=not bool(os.environ.get("LOCATION")))
    loc.add_argument(
        "--location",
        "-l",
        default=os.environ.get("LOCATION"),
        help='Place name, e.g. "Santa Monica, CA" (default: $LOCATION)',
    )
    loc.add_argument("--lat", type=float, help="Latitude (use with --lon)")
    parser.add_argument("--lon", type=float, help="Longitude (use with --lat)")
    parser.add_argument(
        "--count", "-n", type=int, default=5, help="Number of results (default 5)"
    )
    parser.add_argument(
        "--with-spotter",
        action="store_true",
        help="Only show sites with an active spotter sensor",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    args = parser.parse_args()

    if args.lat is not None and args.lon is None:
        parser.error("--lon required with --lat")
    if args.lon is not None and args.lat is None:
        parser.error("--lat required with --lon")

    if args.location:
        email_from = os.environ.get("EMAIL_FROM")
        if not email_from:
            print("Error: EMAIL_FROM not set", file=sys.stderr)
            sys.exit(1)
        _, contact_email = parseaddr(email_from)
        user_agent = f"fishing-conditions-notice/1.0 ({contact_email})"
        if not args.json:
            print(f"Geocoding {args.location!r}...")
        lat, lon = geocode(args.location, user_agent)
        if not args.json:
            print(f"  → {lat:.4f}, {lon:.4f}")
    else:
        lat, lon = args.lat, args.lon

    if not args.json:
        print("Fetching sites...")
    sites = fetch_sites(args.with_spotter)

    ranked = []
    for site in sites:
        coords = site["polygon"]["coordinates"]  # [lon, lat] GeoJSON order
        site_lon, site_lat = coords[0], coords[1]
        dist = haversine_miles(lat, lon, site_lat, site_lon)
        ranked.append((dist, site))

    ranked.sort(key=lambda x: x[0])
    top = ranked[: args.count]

    if args.json:
        results = []
        for dist, site in top:
            coords = site["polygon"]["coordinates"]
            results.append(
                {
                    "id": site["id"],
                    "name": site["name"],
                    "distance_miles": round(dist, 1),
                    "lat": coords[1],
                    "lon": coords[0],
                    "has_spotter": bool(site.get("sensorId")),
                }
            )
        print(json.dumps(results))
        return

    console = Console()
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right", style="dim")
    table.add_column("ID", justify="right")
    table.add_column("Distance", justify="right")
    table.add_column("Lat", justify="right")
    table.add_column("Lon", justify="right")
    table.add_column("Name")
    table.add_column("Spotter", justify="center")

    for i, (dist, site) in enumerate(top, 1):
        coords = site["polygon"]["coordinates"]
        site_lon, site_lat = coords[0], coords[1]
        spotter = "[green]✓[/green]" if site.get("sensorId") else ""
        table.add_row(
            str(i),
            str(site["id"]),
            f"{dist:.1f}mi",
            f"{site_lat:.4f}",
            f"{site_lon:.4f}",
            site["name"],
            spotter,
        )

    console.print(table)


if __name__ == "__main__":
    main()
