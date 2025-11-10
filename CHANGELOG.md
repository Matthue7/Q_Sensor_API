# Changelog

All notable changes to the Q-Sensor API project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2025-11-10

### Added
- Complete FastAPI REST/WebSocket interface for Q-Series sensor control
- DataFrame recording layer with CSV/Parquet export capabilities
- BlueOS extension integration with service discovery
- Docker deployment optimized for Raspberry Pi 4 (ARMv7)
- React webapp UI integration for browser-based control
- 10 hardware validation scripts with comprehensive test coverage
- Full test suite using pytest and FakeSerial device simulator
- Comprehensive documentation (13 markdown files)
- Demo scripts showing freerun, polled, and pause/resume modes
- Diagnostic tools for serial port debugging

### Production Status
- Successfully deployed and tested on Raspberry Pi 4 with BlueOS
- Compatible with Q-Series firmware 4.003 (2150 mode)
- Auto-start DataRecorder on acquisition start
- Thread-safe buffer management and concurrent access
- CORS configuration for cross-origin web access
- Static file serving for React webapp assets

### Technical Details
- Python 3.10+ required for Docker deployment
- pandas 1.3.5 for ARMv7 compatibility
- FastAPI 0.104.0+ for async HTTP/WebSocket
- pyserial 3.5+ for serial communication
- Supports freerun and polled acquisition modes
- Pause/resume capability during acquisition
- Real-time data streaming via WebSocket

## [Unreleased]

### Changed
- Reorganized repository structure (examples/ and tools/ directories)
- Resolved pandas version conflict (standardized on 1.3.5)
- Cleaned up unused dependencies (numpy, requests)
- Updated .gitignore for development artifacts
