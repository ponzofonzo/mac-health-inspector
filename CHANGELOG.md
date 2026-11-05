# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-11-05

### Added
- Ten read-only checks: macOS version, hardware, uptime, boot volume free space, memory pressure,
  CPU load, battery health, SMART status, recent kernel panics, and MDM enrollment.
- JSON, self-contained HTML, and console reports, with `--fail-on` exit-code policy.
- Replay mode for rendering reports from synthetic or recorded fixtures on any OS.
- Unit tests with mocked command output. CI on Ubuntu and macOS, plus a live smoke run.
