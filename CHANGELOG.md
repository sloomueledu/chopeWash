# **CHANGELOG**  

## v0.1.0  
**Commit Date:** 5 October 2026  

**Description:**  

[Initial Commit]  
* Adding Basic files for refinement in the future. Gemini Code contains bugs that needs to be fixed.  

**Additional Remarks:**  
* Do **NOT** use this version for live testing!  

## v0.1.1  
**Commit Date:** 5 October 2026  

**Description:**  

[Bug Fix]  
* Fixed formatting issues in CHANGELOG.md and README.md files.  

**Additional Remarks:**  
* Do **NOT** use this version for live testing!  

## v0.2.0   
**Commit Date:** 5 October 2026  

**Description:**  

[New Files]  
* chopeWash_demo.py - for demo purposes only  
* chopeWash.py - Intended to be the deployed code  

[Implementation Changes]  
* Fixed code implentation with the help of Gemini Pro 3.1  

## v0.3.0  
**Commit Date:** 10 October 2026  

**Description:**  

[New Feature]  
* Added a 3 strike system for queue users to prevent system abuse  

## v0.4.0  
**Commit Date:** 11 October 2026  

**Description:**  

[New Feature]  
* Added Adafruit Functionality for ESP32 & SwitchBot Control  
[New File]  
* Added chopeWash_ESP32.ino for ESP32 Code  

## v0.5.0
**Commit Date:** 11 October 2026

**Description**

[New Features & UI]
* Dynamic Countdowns: Telegram dashboard now displays live remaining time to start machines and claim finished laundry.
* Escalated Abandonment Ban: Applies a 72-hour ban if a user abandons clothes in both the washer and dryer during a "Wash & Dry" cycle (standard single-machine abandonment remains 48 hours).
* Queue Turn Strike Ban: Implemented a 2-hour queue lockout upon accumulating 3 turn strikes.

[Hardware & Firmware]
* TLS Trust Anchor: Updated ESP32 CA certificate to DigiCert Global Root G2 for secure MQTTS on Port 8883.
* Topic Normalization: Unified MQTT feed topics to lowercase across ESP32 and Python services.

[Bug Fixes]
* Ban Notice Consistency: Fixed 3rd-strike Telegram message displaying 48 hours instead of 2 hours.  

[File Update]  
* Updated requirements.txt  

## v1.0.0
**Commit Date:** 12 October 2026

**Description**

[New Features]
* Added OLED Display Capabilities to the ESP32   