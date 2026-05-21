"""Interactive setup — collects config, writes .env, pushes vars/secret to GitHub."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import questionary
import requests
from dotenv import dotenv_values

ROOT = Path(__file__).parent.parent
BASE_URL = "https://ocean-systems.uc.r.appspot.com/api"

SMTP_PRESETS = {
    "gmail": ("smtp.gmail.com", "587", ""),
    "resend": ("smtp.resend.com", "587", "resend"),
}

VARS = [
    "LOCATION",
    "AQUALINK_SITE_ID",
    "WATER_TEMPERATURE_THRESHOLD",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
]
SECRETS = [
    "SMTP_PASSWORD",
    "EMAIL_FROM",
    "EMAIL_TO",
]


def load_existing() -> dict:
    env_file = ROOT / ".env"
    if env_file.exists():
        return dict(dotenv_values(env_file))
    return {}


def fetch_site_meta(site_id: str) -> dict:
    resp = requests.get(f"{BASE_URL}/sites/{site_id}", timeout=10)
    resp.raise_for_status()
    return resp.json()


def find_site(prev: dict) -> str:
    prev_id = prev.get("AQUALINK_SITE_ID", "")
    if prev_id:
        meta = fetch_site_meta(prev_id)
        keep = questionary.confirm(
            f"Current site: {meta.get('name')} (id={prev_id}) — keep it?",
            default=True,
        ).ask()
        if keep:
            return prev_id

    mode = questionary.select(
        "Find your nearest Aqualink site by:",
        choices=["Location name", "Lat / lon"],
    ).ask()

    if mode == "Lat / lon":
        lat = questionary.text(
            "Latitude", validate=lambda v: _is_float(v) or "Must be a number"
        ).ask()
        lon = questionary.text(
            "Longitude", validate=lambda v: _is_float(v) or "Must be a number"
        ).ask()
        args = ["--lat", lat, "--lon", lon]
    else:
        location = questionary.text("Location", placeholder="e.g. Santa Monica, CA").ask()
        args = ["--location", location]

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "find-nearest-site.py"),
            *args,
            "--count",
            "10",
            "--json",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stderr)
        sys.exit(1)

    sites = json.loads(result.stdout)
    choices = [
        questionary.Choice(
            f"{s['name']}  ({s['distance_miles']}mi, id={s['id']}{'  *' if s['has_spotter'] else ''})",
            value=str(s["id"]),
        )
        for s in sites
    ]
    return questionary.select("Select site", choices=choices).ask()


def collect_config(prev: dict) -> dict:
    location = questionary.text(
        "Fishing location — e.g. 'Santa Monica, CA'",
        default=prev.get("LOCATION", ""),
        validate=_required,
    ).ask()

    site_id = find_site(prev)

    threshold = questionary.text(
        "Temperature threshold (°F) — leave blank to always send",
        default=prev.get("WATER_TEMPERATURE_THRESHOLD", ""),
        validate=lambda v: v == "" or _is_float(v) or "Must be a number or blank",
    ).ask()

    prev_host = prev.get("SMTP_HOST", "")
    prev_provider = next(
        (k for k, (host, _, _) in SMTP_PRESETS.items() if host == prev_host), "resend"
    )
    smtp_choices = [
        questionary.Choice("Resend", value="resend"),
        questionary.Choice("Gmail", value="gmail"),
    ]
    provider = questionary.select(
        "SMTP provider",
        choices=smtp_choices,
        default=next((c for c in smtp_choices if c.value == prev_provider), smtp_choices[0]),
    ).ask()

    smtp_host, smtp_port, _ = SMTP_PRESETS[provider]

    email_from = questionary.text(
        "From address", default=prev.get("EMAIL_FROM", ""), validate=_required
    ).ask()
    email_to = questionary.text(
        "To address(es) — comma-separated for multiple",
        default=prev.get("EMAIL_TO", email_from),
        validate=_required,
    ).ask()

    match provider:
        case "resend":
            smtp_user = "resend"
        case "gmail":
            smtp_user = email_from

    smtp_password = questionary.password(
        "SMTP password / API key (leave blank to keep existing)",
        validate=lambda v: True if v or prev.get("SMTP_PASSWORD") else "Required",
    ).ask()

    return {
        "LOCATION": location,
        "AQUALINK_SITE_ID": site_id,
        "WATER_TEMPERATURE_THRESHOLD": threshold,
        "SMTP_HOST": smtp_host,
        "SMTP_PORT": smtp_port,
        "SMTP_USER": smtp_user,
        "SMTP_PASSWORD": smtp_password or prev.get("SMTP_PASSWORD", ""),
        "EMAIL_FROM": email_from,
        "EMAIL_TO": email_to,
    }


def write_env(config: dict) -> None:
    env_file = ROOT / ".env"
    example_file = ROOT / ".env.example"
    lines = []
    for line in example_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key = line.split("=", 1)[0]
            lines.append(f"{key}={config.get(key, '')}")
        else:
            lines.append(line)
    env_file.write_text("\n".join(lines) + "\n")
    print(f"Wrote {env_file}")


def gh_available() -> bool:
    return subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0


def push_to_github(config: dict, dry_run: bool = False) -> None:
    if not dry_run and not gh_available():
        print("gh CLI not found or not authenticated.")
        print("Run `gh auth login` then `make setup` again to push to GitHub.")
        return

    for key in VARS:
        val = config.get(key, "")
        if not val:
            continue
        if dry_run:
            print(f"  [dry-run] gh variable set {key} --body {val!r}")
        else:
            subprocess.run(["gh", "variable", "set", key, "--body", val], check=True)
            print(f"  var    {key}")

    for key in SECRETS:
        val = config.get(key, "")
        if not val:
            continue
        if dry_run:
            print(f"  [dry-run] gh secret set {key} --body ***")
        else:
            subprocess.run(["gh", "secret", "set", key, "--body", val], check=True)
            print(f"  secret {key}")


def _is_float(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return False


def _required(v: str) -> bool | str:
    return True if v.strip() else "Required"


def example_keys() -> set[str]:
    return {
        line.split("=", 1)[0]
        for line in (ROOT / ".env.example").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true", help="Show gh commands without executing them"
    )
    parser.add_argument(
        "--reformat",
        action="store_true",
        help="Rewrite .env using .env.example structure; no prompts",
    )
    args = parser.parse_args()

    print("=== Fishing Conditions Notice Setup ===\n")

    if args.reformat:
        config = load_existing()
        if not config:
            print("No .env found — run without --reformat to set up.")
            sys.exit(1)
        expected = example_keys()
        env_keys = set(config.keys())
        if orphaned := env_keys - expected:
            print(f"Warning: keys in .env not in .env.example: {', '.join(sorted(orphaned))}")
        if missing := expected - env_keys:
            print(f"Warning: keys in .env.example not in .env: {', '.join(sorted(missing))}")
        write_env(config)
        return

    if args.dry_run:
        print("Dry-run mode — no gh calls will be made.\n")

    prev = load_existing()
    if prev:
        print("Found existing .env — values pre-filled as defaults.\n")

    config = collect_config(prev)

    print()
    write_env(config)

    push_label = (
        "[dry-run] Push vars/secrets to GitHub Actions?"
        if args.dry_run
        else "Push vars/secrets to GitHub Actions?"
    )
    push = questionary.confirm(push_label, default=True).ask()
    if push:
        push_to_github(config, dry_run=args.dry_run)

    print("\nDone. Run `make dev` to preview your notice.")


if __name__ == "__main__":
    main()
