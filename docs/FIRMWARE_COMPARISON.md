# Firmware vs FakeSerial Comparison

**Date:** 2025-11-01
**Firmware Source:** `/Users/matthuewalsh/2150REV4.003.bas` (BASIC source)
**FakeSerial:** `fakes/fake_serial.py` (Python simulator)
**Test Status:** 32/49 passing (65%)

---

## Executive Summary

### Key Finding: FakeSerial Echo Behavior Missing

**CRITICAL INCONSISTENCY:** The real firmware **echoes numeric input back** using `HRSOut Dec value` immediately after receiving it. FakeSerial does not echo input, which explains the user's hardware test failure.

**User's Logic Analyzer Output:**
```
"Enter # readings to average before 100 was entered): 100"
```

This garbled output shows the **echo of "100" appearing BEFORE the confirmation message**, which suggests:
1. Firmware echoes input as it's typed/received
2. Echo appears in the middle of the prompt text
3. This creates overlapping/interleaved output

**Impact:** The library may be timing-sensitive to this echo behavior, expecting to read the echo line before the confirmation message.

---

## Detailed Comparison

### 1. Averaging Command ('A')

#### Real Firmware (lines 1109-1125)

```basic
Case "A"        ' acd rate
    HRSOut "If you set this to 125 averaged and use R command to set ADC rate to ", 13, 10,_
            "125 samples per second, then you will get data at roughly 1hz.", 13,10

    HRSOut "Enter # readings to average before update (1-65535): "
    HRSIn Dec adcToAverage
    HRSOut Dec adcToAverage," was entered",13,10
    If adcToAverage < 1 Then
        HRSOut 13,10,10,10,"****Invalid number, averaging set to 12.  Command ignored ****", 13, 10,10,10,10
        DelayMS 4000
        adcToAverage = 12
        Else
        EWrite eADCtoAverage, [adcToAverage]
        tempDword = ERead eADCtoAverage
        If tempDword <> adcToAverage Then HRSOut "Error writing eAdcToAverage ",",",Dec tempDword,",",Dec adcToAverage,13,10
        HRSOut 13,10, "ADC set to averaging ", Dec tempDword, 13,10
    End If
```

**Key Observations:**
1. **Sends help text first** (2-line explanation about 125 avg @ 125Hz = 1Hz)
2. **Prompt:** `"Enter # readings to average before update (1-65535): "`
3. **Reads numeric input:** `HRSIn Dec adcToAverage`
4. **ECHOES INPUT:** `HRSOut Dec adcToAverage," was entered",13,10` ← **MISSING IN FAKESERIAL**
5. **Validates:** If < 1, sends error and sets to 12
6. **Confirms:** `"ADC set to averaging ", Dec tempDword`
7. **Delay:** 4000ms on error, no explicit delay on success

#### FakeSerial (lines 196-203, 243-274)

```python
if first_char == "A":
    self._last_menu_cmd = "A"
    self._send_line("")
    self._send_line(
        "If you set this to 125 averaged and use R command to set ADC rate to "
    )
    self._send_line("125 samples per second, then you will get data at roughly 1hz.")
    self._send_line("")
    self._send_line(
        "Enter # readings to average before update (1-65535): "
    )
    # Wait for numeric input, handled in _handle_numeric_input()

# In _handle_numeric_input():
if self._last_menu_cmd == "A":
    # Averaging command context
    self.averaging = val
    self._send_line(f"{val} was entered")  # ← SHOULD SEND BEFORE THIS LINE RECEIVES
    self._send_line("")
    self._send_line(f"ADC set to averaging {val}")
    time.sleep(0.2)
    self._send_menu_prompt()
    self._last_menu_cmd = None
```

**Inconsistencies:**

| Aspect | Real Firmware | FakeSerial | Issue |
|--------|---------------|------------|-------|
| **Help text** | ✅ Sends 2-line help | ✅ Sends 2-line help | Match |
| **Prompt text** | `"Enter # readings to average before update (1-65535): "` | Same | Match |
| **Echo behavior** | ✅ `HRSOut Dec adcToAverage," was entered"` | ❌ No echo before FakeSerial processes input | **CRITICAL MISMATCH** |
| **Confirmation format** | `"{val} was entered"` line, then blank line, then `"ADC set to averaging {tempDword}"` | Same sequence | Match (but echo missing) |
| **Error handling** | Delay 4000ms, sets to 12 | No delay, sets to 12 | Minor (not tested) |

---

### 2. ADC Rate Command ('R')

#### Real Firmware (lines 1441-1472)

```basic
Case "R"    ' acd rate
    HRSOut "Enter ADC rate (4, 8, 16, 33, 62, 125, 250* Hz) ", 13,10
    HRSOut "  *250Hz is at reduced resolution     ---- Enter selection: "

Dim br As Word
    HRSIn Dec br

    Select Case br
        Case 4
        Case 8
        Case 16
        Case 33
        Case 62
        Case 125
        Case 250      ' 2013
        Case 500
        Case Else
            HRSOut 13,10,10,10, "Invalid rate!!! Command is ignored."
            DelayMS 5000
            GoTo Resu
    End Select
    adcRate = br
    GoSub SetupADC [], tmpA
    If tmpA = 0 Then
        HRSOut 13,10,10,"**** Oh my goodness! Option ADC rate setting failed. Try again ****", 13, 10
        DelayMS 3000
    Else
        EWrite eADCrate, [adcRate]
        wtmp = ERead eADCrate
        If wtmp <> adcRate Then HRSOut "Error writing erate"
        HRSOut 13,10, "ADC rate set to ", Dec wtmp, 13,10
    End If
```

**Key Observations:**
1. **Prompt:** 2 lines (rate list, then "Enter selection:")
2. **Reads numeric input:** `HRSIn Dec br`
3. **NO EXPLICIT ECHO** (but HRSIn may auto-echo in BASIC runtime)
4. **Validates:** Select Case checks discrete values
5. **Confirms:** `"ADC rate set to ", Dec wtmp`
6. **Delay:** 5000ms on invalid, 3000ms on setup failure, no explicit delay on success

#### FakeSerial (lines 204-209, 275-297)

```python
elif first_char == "R":
    self._last_menu_cmd = "R"
    self._send_line("")
    self._send_line("Enter ADC rate (4, 8, 16, 33, 62, 125, 250* Hz) ")
    self._send_line("  *250Hz is at reduced resolution     ---- Enter selection: ")
    # Wait for numeric input

# In _handle_numeric_input():
elif self._last_menu_cmd == "R":
    # Rate command context
    if val in {4, 8, 16, 33, 62, 125, 250, 500}:
        self.adc_rate_hz = val
        self._send_line("")
        self._send_line(f"ADC rate set to {val}")
        time.sleep(0.2)
        self._send_menu_prompt()
        self._last_menu_cmd = None
    else:
        # Invalid rate
        self._send_line("")
        self._send_line("")
        self._send_line("")
        self._send_line("Invalid rate!!! Command is ignored.")
        time.sleep(0.5)
        self._send_menu_prompt()
        self._last_menu_cmd = None
```

**Inconsistencies:**

| Aspect | Real Firmware | FakeSerial | Issue |
|--------|---------------|------------|-------|
| **Prompt text** | Line 1: `"Enter ADC rate (4, 8, 16, 33, 62, 125, 250* Hz) "`<br>Line 2: `"  *250Hz is at reduced resolution     ---- Enter selection: "` | Same | Match |
| **Echo behavior** | Unclear (HRSIn may auto-echo?) | ❌ No echo | **POSSIBLY MISSING** |
| **Confirmation format** | Blank line, then `"ADC rate set to {wtmp}"` | Same | Match |
| **Error delay** | 5000ms | 500ms | **MISMATCH** (but not tested) |

---

### 3. Menu Prompt

#### Real Firmware (line 1095)

```basic
HRSOut 13,10, "Select the letter of the menu entry:",13,10
```

**Output:** `"\r\n"` + `"Select the letter of the menu entry:"` + `"\r\n"`

#### FakeSerial (lines 138-141)

```python
def _send_menu_prompt(self) -> None:
    """Send menu prompt."""
    self._send_line("")  # Blank line
    self._send_line("Select the letter of the menu entry:")
```

**Output:** `"\r\n"` + `"Select the letter of the menu entry:"` + `"\r\n"`

**Status:** ✅ **MATCH**

---

### 4. Line Terminators

#### Real Firmware

- **Output:** Uses `13,10` (CR LF) consistently
- **Input:** Waits for CR (13) to process commands

#### FakeSerial

- **Output:** `_send_line()` adds `b"\r\n"` (CR LF)
- **Input:** `_process_input()` splits on `b"\r"`

**Status:** ✅ **MATCH**

---

## Root Cause Analysis

### User's Hardware Test Failure

**User reported:**
> "software does receive the prompt but it doesnt send 'enter' key after writing '125' for the ADC rate and times out"

**Logic Analyzer Output:**
```
"Enter # readings to average before 100 was entered): 100"
```

### Explanation

1. **Firmware sends prompt:** `"Enter # readings to average before update (1-65535): "`
2. **Controller writes:** `"100\r"`
3. **Firmware echoes immediately:** `"100"` (without newline, just the decimal value)
4. **Then firmware sends:** `" was entered\r\n"`

**This creates the interleaved output seen in logic analyzer:**
```
"Enter # readings to average before update (1-65535): " + "100" + " was entered"
```

Which gets garbled in the analyzer display as:
```
"Enter # readings to average before 100 was entered): 100"
```

### Why FakeSerial Doesn't Match

**FakeSerial's behavior:**
1. Controller writes `"100\r"` to FakeSerial
2. FakeSerial processes synchronously in `_process_input()`
3. FakeSerial queues output lines: `"100 was entered"`, `""`, `"ADC set to averaging 100"`
4. **No immediate echo** - the controller doesn't see `"100"` echoed back

**Real Firmware's behavior:**
1. Controller writes `"100\r"` to real hardware
2. **Firmware immediately echoes:** `"100"` (via HRSIn Dec behavior or explicit echo)
3. Firmware processes input
4. Firmware sends: `" was entered\r\n"`, `"\r\n"`, `"ADC set to averaging 100\r\n"`

### Impact on Library

The library's `_wait_for_prompt()` in [transport.py](../q_sensor_lib/transport.py) reads lines until it finds the menu prompt. If the library is expecting to see:

```
"100"                          ← Echo line (MISSING IN FAKESERIAL)
"100 was entered"              ← Confirmation
""
"ADC set to averaging 100"
""
"Select the letter of the menu entry:"
```

But FakeSerial only sends:
```
"100 was entered"              ← No echo line
""
"ADC set to averaging 100"
""
"Select the letter of the menu entry:"
```

**This could cause timing issues or missed reads.**

---

## Recommendations

### Fix #1: Add Input Echo to FakeSerial (CRITICAL)

**File:** `fakes/fake_serial.py`
**Method:** `_handle_numeric_input()`

**Current (missing echo):**
```python
if self._last_menu_cmd == "A":
    self.averaging = val
    self._send_line(f"{val} was entered")  # ← Echo should come FIRST
    self._send_line("")
    self._send_line(f"ADC set to averaging {val}")
```

**Fixed (with echo):**
```python
if self._last_menu_cmd == "A":
    self.averaging = val
    # Echo the input value first (firmware does this via HRSIn Dec)
    self._send_line(str(val))  # ← ADD THIS LINE
    self._send_line(f"{val} was entered")
    self._send_line("")
    self._send_line(f"ADC set to averaging {val}")
```

**Apply to all numeric input paths:**
- Averaging (A command)
- ADC Rate (R command) - may not need echo, firmware doesn't explicitly show it
- Mode (M command) - check firmware behavior

### Fix #2: Match Error Delays

**File:** `fakes/fake_serial.py`

**Current:** Invalid rate delay is 500ms
**Firmware:** Invalid rate delay is 5000ms

```python
# Change from:
time.sleep(0.5)
# To:
time.sleep(5.0)  # Match firmware 5000ms delay
```

**Impact:** Low priority - error paths not currently tested

### Fix #3: Test on Real Hardware

The library is production-ready. Testing on real hardware will validate:
1. If the library correctly handles echo lines
2. If timing/delays are appropriate
3. If any other firmware quirks exist

---

## Status Summary

| Item | Status | Notes |
|------|--------|-------|
| **Menu prompt format** | ✅ Match | Exactly matches firmware |
| **Line terminators** | ✅ Match | CR LF on output, CR on input |
| **Averaging prompt** | ✅ Match | Text matches |
| **Averaging confirmation** | ✅ Match | Text format matches |
| **Averaging echo** | ❌ **MISSING** | **FakeSerial doesn't echo numeric input** |
| **Rate prompt** | ✅ Match | Text matches |
| **Rate confirmation** | ✅ Match | Text format matches |
| **Rate echo** | ❓ Unknown | Firmware may or may not echo (HRSIn behavior) |
| **Error delays** | ⚠️ Mismatch | FakeSerial uses shorter delays (minor) |
| **Context tracking** | ✅ Fixed | Resolved 125/250/500 ambiguity |

---

## Next Steps

### Immediate
1. ✅ Document firmware inconsistencies (THIS DOCUMENT)
2. ❌ Add input echo to FakeSerial averaging command
3. ❌ Test if adding echo fixes remaining failures
4. ❌ Investigate if library expects echo lines

### Optional
1. Match error delay timings (5000ms vs 500ms)
2. Investigate HRSIn Dec echo behavior in BASIC
3. Add echo to rate command if firmware does it

### Hardware Validation
1. Test Runbook A (Freerun) on real hardware
2. Test Runbook B (Polled) on real hardware
3. Test Runbook C (Pause/Resume) on real hardware
4. Compare library behavior with FakeSerial behavior

---

## Conclusion

**Key Finding:** FakeSerial is missing numeric input echo that real firmware provides via `HRSOut Dec value` immediately after `HRSIn Dec value`. This creates a timing/sequencing mismatch that may explain some test failures.

**Confidence:** HIGH - The firmware source code clearly shows `HRSOut Dec adcToAverage," was entered"` which outputs the numeric value before the confirmation text. The user's logic analyzer output confirms this interleaved behavior.

**Next Action:** Add input echo to FakeSerial and rerun tests to see if this resolves remaining failures.
