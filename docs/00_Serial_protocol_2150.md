# Q-Series Sensor Serial Command/Response Protocol Documentation

**Firmware Version 4.003 - Digital Output, Linear (2150 Mode)**

---

## Deliverable 1: Command/Response Table

### General Protocol Notes

- **Line Termination**: Device expects **CR (carriage return, 0x0D)** as input terminator. When sending commands that require text input (numbers, strings), the device uses `HRSIn` which reads until CR.
- **Echo Behavior**: Device **does NOT echo** single menu command characters automatically. For text input prompts (numbers), characters are not echoed either.
- **Output Format**: Device sends **CRLF (0x0D 0x0A)** for line endings in all output messages.
- **Case Sensitivity**: Menu commands are case-**insensitive** (converted to uppercase internally via `ToUpper`).
- **Timing**: 1-second delay before menu display (`DelayMS 1000` at line 1092).

### Command Reference Table

| Operation | Send (bytes) | Expected Device Prompt/Reply (verbatim or regex) | Mode Prereq | Success Indicator | Notes (echo, timing, retries) |
|-----------|--------------|--------------------------------------------------|-------------|-------------------|-------------------------------|
| **Enter config menu** | `0x1B` (ESC) or `?` | Multi-line signon banner ending with:<br>`"Select the letter of the menu entry:"`<br>CR LF | Any (freerun/polled/streaming) | Signon banner appears showing version, serial, mode, current settings | Interrupts sampling. Firmware displays full menu after 1-second delay. No echo. ESC=27 decimal. Both ESC and `?` trigger `ProcessMenu` (lines 708, 791). |
| **Read current config snapshot** | `^` (caret) | Device sends CSV config string starting with:<br>`<avg>,<baud>,<CalFactor>,...,<OperatingMode>,<sTAG>,...`<br>then returns to menu prompt | Config menu | CSV line printed (line 1668-1689) | No prompt—silent parameter dump. Format defined in `SendAllParameters`. Does not exit menu. |
| **Set Averaging (A)** | `A`<br>then:<br>`<number>CR` | Prompt:<br>`"Enter # readings to average before update (1-65535): "`<br>After input:<br>`"<number> was entered"`<br>`"ADC set to averaging <number>"` CR LF | Config menu | `"ADC set to averaging <N>"` | If value < 1, device prints:<br>`"****Invalid number, averaging set to 12. Command ignored ****"` and waits 4 seconds (line 1117-1118). Valid range: 1–65535. No echo on number entry. |
| **Set Sampling Rate (R)** | `R`<br>then:<br>`<rate>CR` | Prompt:<br>`"Enter ADC rate (4, 8, 16, 33, 62, 125, 250* Hz)"` CR LF<br>`" *250Hz is at reduced resolution ---- Enter selection: "`<br>Success:<br>`"ADC rate set to <rate>"` CR LF | Config menu | `"ADC rate set to <rate>"` | Valid rates: 4, 8, 16, 33, 62, 125, 250, 500 Hz (lines 1449-1456). Invalid rate:<br>`"Invalid rate!!! Command is ignored."` and 5-second delay. If ADC config fails:<br>`"**** Oh my goodness! Option ADC rate setting failed. Try again ****"` (line 1465). |
| **Set Mode (M → Polled)** | `M`<br>then:<br>`1CR`<br>then:<br>`<TAG_char>CR` | Prompt:<br>`"Set operating mode. Mode 0 is freerun, 1 is polled. Polled require a TAG to be defined"` CR LF<br>`"Enter the operating mode number: "`<br>After `1`:<br>`"Enter the single character that will be the tag used in polling (A-F) UPPER case"` CR LF<br>`"Note tags G-Z may not be supported in some Biospherical acquisition software : "` | Config menu | TAG stored, no explicit "success" message printed | TAG must be A-Z (ASCII 65-90). Invalid TAG prints:<br>`" Bad TAG "` (line 1372). Polled mode symbol: `"1"` (line 387). |
| **Set Mode (M → Freerun)** | `M`<br>then:<br>`0CR` | Prompt:<br>`"Set operating mode. Mode 0 is freerun, 1 is polled. Polled require a TAG to be defined"` CR LF<br>`"Enter the operating mode number: "`<br>Device echoes:<br>`0` CR LF | Config menu | Device echoes `0` (line 1358) | Freerun mode symbol: `"0"` (line 388). If input is not `0` or `1`, device prints:<br>`"I am confused"` (line 1375). |
| **Exit menu (X)** | `X` | Output:<br>`"Calling reset!"`<br>`"Rebooting program"` CR LF<br>Then: device **resets** (calls `Reset`) | Config menu | Device reboots and re-enters sampling mode per saved `OperatingMode` | **CRITICAL**: `X` triggers full MCU reset (line 1569, 1589). Expect power-on banner unless `QuietMode=1`. Approx 100ms delay before signon. |
| **Pause acquisition** | `0x1B` or `?` | Signon banner + menu | Freerun streaming | Menu appears, streaming stops | Same as "Enter config menu". Interrupts disabled (line 712-713). |
| **Stop acquisition** | N/A | N/A | N/A | N/A | No explicit "stop" command. Use ESC/`?` to enter menu, then `X` to reboot and re-enter selected mode. |
| **Resume acquisition** | `X` | `"Calling reset!"` etc. | Menu (after pause) | Device resets and resumes per `OperatingMode` | Same as "Exit menu (X)". Full reboot cycle. |
| **Polled: single query** | `>` then `<TAG>` | Single data line:<br>`<TAG>,<preamble><calibrated_value>[,<temp>][,<Vin>]` CR LF | Polled mode only | Data line starting with TAG received | **Two-character sequence**: First `>` (line 800-802), then TAG character (lines 804-810). Device waits in `While Queried = 0` loop (line 626) until query received. Interrupt handler sets `Queried=1` (line 807). Must send `*<TAG>Q000!` first to start averaging (lines 592-596), otherwise no data accumulation. |
| **Re-enter menu from stream** | `0x1B` or `?` | Signon banner + menu | Freerun streaming | Menu displayed | Identical to "Pause acquisition". Firmware monitors `PIR1.5` (UART RX interrupt flag) continuously (line 527). Interrupt handler jumps to `ProcessMenu` (line 715, 798). |

---

## Deliverable 2: Menu Navigation Flow

### Power-On / Reset Behavior

```
[DEVICE RESET/POWER-ON]
    ↓
[If QuietMode = 0: Print signon banner]
    Banner format (lines 872-920):
        "Biospherical Instruments Inc: Digital Engine Vers 4.003" CR LF
        "Unit ID <serial_number>" CR LF
        "Operating in <mode> mode" CR LF        ← "free run mode" OR "polled mode with tag of <TAG>"
        "ADC sample rate <rate>, gain <gain>" CR LF
        "Averaging <N> readings" CR LF
        ... [temp, voltage, calfactor, etc.]
    ↓
[Initialize ADC]
    If ADC setup fails (line 468):
        "SetupADC did not complete correctly" → jump to ProcessMenu
    Else if startup check fails (line 478):
        "ADC did not startup ok"
    Success (line 480):
        "ADC OK" (if QuietMode = 0)
    ↓
[Branch based on OperatingMode] (lines 503-507)
    If OperatingMode = "1" (opPolled) → GoTo Polled
    Else (OperatingMode = "0" or other) → GoTo StartFreerunSampling
```

### Entering Configuration Menu (ESC or ?)

```
[User sends 0x1B or '?']
    ↓
[Interrupt handler ComInterrupt2150 / ComInterruptPolled] (lines 787-798, 708-716)
    Disable interrupts (INTCON.7=0, INTCON.6=0)
    Clear FirstChar
    GoSub ProcessMenu
    ↓
[ProcessMenu subroutine] (lines 1075-1587)
    Turn on RS232 driver (PORTC.0 = High)
    Call SignonMessage → prints full banner again
    ↓
[Menu Loop: While tmpChar <> "X"] (line 1090)
    DelayMS 1000
    Call SendMenu
        Prints (lines 1598-1650):
            "Biospherical Instruments Inc: Digital Log Engine v: 4.003" CR LF CR LF
            "Model: <serial>" CR LF
            "A to set number of samples averaged before update: <current>" CR LF
            "B to set the baudrate, now: <baud>" CR LF
            "C to set the Calibration Factor for digital output: <CalFactor>" CR LF
            "D to set the description available for display in software: <desc>" CR LF
            ...
            "M to set the operating mode (0=streaming, 1=polled with tag= <TAG>) currently <mode>" CR LF
            "N to set analog output mode: <Digital only | Linear analog | LOG analog>" CR LF
            "O to configure the OUTPUTs, temperature is <enabled/disabled>, line voltage is <enabled/disabled>" CR LF
            ...
            "R to set ADC sample rate: <rate>" CR LF
            ...
            "X to restart sampling" CR LF
            ...
    Print: "Select the letter of the menu entry:" CR LF
    ↓
[Wait for user input] (lines 1096-1101)
    Repeat reading HRSIn until character != CR (line 1098 filters LF)
    Convert to uppercase
    Print CR LF (start new line)
    ↓
[Select Case tmpChar] (lines 1104-1577)
    Case "X" → GoTo EndMenu → print "Rebooting program" → Reset
    Case "A" → Averaging submenu (see below)
    Case "R" → Sampling rate submenu (see below)
    Case "M" → Mode submenu (see below)
    Case "^" → SendAllParameters (CSV dump)
    Case "?" or ESC → SendMenu (redisplay)
    ... [other options]
    ↓
[Loop back to "Select the letter of the menu entry:" until X pressed]
```

### Averaging (A) Submenu Flow

```
User sends: 'A' CR
    ↓
Device prints (lines 1110-1113):
    "If you set this to 125 averaged and use R command to set ADC rate to" CR LF
    "125 samples per second, then you will get data at roughly 1hz." CR LF
    "Enter # readings to average before update (1-65535): "
    ↓
Device waits: HRSIn Dec adcToAverage  (blocks until CR received)
    ↓
Device validates (lines 1116-1125):
    If adcToAverage < 1:
        Print: CR LF CR LF CR LF "****Invalid number, averaging set to 12. Command ignored ****" CR LF CR LF CR LF CR LF
        DelayMS 4000
        Set adcToAverage = 12
    Else:
        EWrite to EEPROM
        Verify write
        Print: CR LF "ADC set to averaging <N>" CR LF
    ↓
Return to main menu loop
```

### Sampling Rate (R) Submenu Flow

```
User sends: 'R' CR
    ↓
Device prints (lines 1442-1443):
    "Enter ADC rate (4, 8, 16, 33, 62, 125, 250* Hz)" CR LF
    " *250Hz is at reduced resolution     ---- Enter selection: "
    ↓
Device waits: HRSIn Dec br  (blocks until CR received)
    ↓
Device validates (lines 1448-1461):
    Select Case br
        Valid: 4, 8, 16, 33, 62, 125, 250, 500
        Invalid:
            Print: CR LF CR LF CR LF "Invalid rate!!! Command is ignored."
            DelayMS 5000
            GoTo Resu (timeout handler)
    ↓
If valid:
    Call SetupADC with new rate (line 1463)
    If setup fails (line 1464):
        Print: CR LF CR LF "**** Oh my goodness! Option ADC rate setting failed. Try again ****" CR LF
        DelayMS 3000
    Else:
        EWrite to EEPROM
        Verify write
        Print: CR LF "ADC rate set to <rate>" CR LF
    ↓
Return to main menu loop
```

### Mode (M) Submenu Flow

```
User sends: 'M' CR
    ↓
Device prints (lines 1353-1354):
    "Set operating mode. Mode 0 is freerun, 1 is polled. Polled require a TAG to be defined" CR LF
    "Enter the operating mode number: "
    ↓
Device waits: HRSIn {20000,Resu}, tmpA  (20-second timeout)
    ↓
If tmpA = "0" (lines 1356-1359):
    Set OperatingMode = opFreeRun (symbol "0")
    Print: 0 CR LF
    EWrite eOperatingMode
    → Return to menu loop
    ↓
ElseIf tmpA = "1" (lines 1360-1373):
    Set OperatingMode = opPolled (symbol "1")
    Print (lines 1362-1363):
        CR LF "Enter the single character that will be the tag used in polling (A-F) UPPER case" CR LF
        "Note tags G-Z may not be supported in some Biospherical acquisition software : "
    Wait: HRSIn {20000, Resu}, tmpA
    Convert to uppercase
    Validate: tmpA > 64 AND tmpA < 91 (ASCII A-Z)
        Valid:
            Set sTAG = tmpChar
            EWrite eTag, eOperatingMode
        Invalid:
            Print: " Bad TAG " CR LF
    → Return to menu loop
    ↓
Else (line 1374-1376):
    Print: "I am confused" CR LF
    → Return to menu loop
```

### Exiting Menu (X) Flow

```
User sends: 'X' CR
    ↓
Case "X" (line 1105) → GoTo EndMenu (line 1106)
    ↓
EndMenu: (lines 1587-1590)
    Print: "Rebooting program" CR LF
    Execute: Reset  (line 1589 - MCU hardware reset)
    [Dead code line 1590 never executes]
    ↓
[Device reboots - jumps back to Startup: label]
    → Power-on sequence repeats
    → Signon banner (if QuietMode=0)
    → Initialize ADC
    → Branch to Polled or StartFreerunSampling based on saved OperatingMode
```

### Distinguishing Polled vs Freerun in Run Mode

#### Freerun Mode Observable Behavior (lines 512-542)

- **Continuous stream** of data lines (one per averaged reading)
- No query command needed
- Format: `<preamble><calibrated_value>[,<temp>][,<Vin>]` CR LF
- Lines appear at rate = `ADC_rate / adcToAverage` (e.g., 125 Hz / 125 avg = 1 Hz)
- Startup message (if QuietMode=0): `"Start free run sampling"` CR LF `"Starting Sampling; quiet mode =0"` CR LF

#### Polled Mode Observable Behavior (lines 547-645)

- **No output until queried**
- Device is silent, waiting in `While 1=1` loop (line 583)
- Startup message (if QuietMode=0): `"Entering polled mainline sampling"` CR LF
- Must send `*<TAG>Q000!` to start accumulating averages (lines 592-596)
- Must send `><TAG>` to trigger data transmission (lines 804-810)
- Response format: `<TAG>,<preamble><calibrated_value>[,<temp>][,<Vin>]` CR LF
- Only one line per query

---

## Deliverable 3: Stream / Frame Format

### Digital Output Mode (LogOutput = 0) - 2150 Linear

**Output Subroutine**: `Sendit` → `SendFormattedVolts` (lines 924-937, 996-1031)

#### Example Stream Line

Assuming preamble="$LITE", temp enabled, Vin disabled, CalibrationMode='B':

```
$LITE123.456789
```

or with temperature:

```
$LITE123.456789, 21.34
```

or with temperature and voltage:

```
$LITE123.456789, 21.34, 12.345
```

#### Polled Mode Example

TAG='A', preamble="", temp/Vin disabled:

```
A,123.456789
```

#### Labeled Parse

```
<preamble>  <calibrated_value>  [, <temp_C>]  [, <Vin_volts>]  CR LF
   ↑              ↑                    ↑              ↑
   User-defined   Float formatted     Optional       Optional
   string stored  per FormatCode      float (2 dec)  float (3 dec)
   in EEPROM      (1-8 digits)
```

#### Field Details

1. **Preamble** (optional, user-configurable via menu option "P")
   - Stored in EEPROM at `ePreamble`
   - Example: `"$LITE"`, `""` (empty)
   - Sent via `GoSub eSend [ePreamble]` (line 926, 997)

2. **Calibrated Value** (main data field)
   - Calculation depends on `CalibrationMode` (lines 999-1010):
     - `'A'`: `Volts - DarkValue` (dark-corrected voltage)
     - `'B'`: `(Volts - DarkValue) / CalFactor` (normal calibrated use in air)
     - `'C'`: `(Volts - DarkValue) / CalFactorWithImmersion` (water use with immersion factor)
     - `'D'`: `Volts` (raw ADC voltage, no correction)
   - Format: `Dec<N> fTemp2` where N = FormatCode (1-8 decimal places, lines 1012-1029)
     - FormatCode auto-calculated based on CalFactor magnitude (lines 413-441)
   - **No comma separates preamble from value** (direct concatenation)

3. **Temperature** (optional, if `TempOutput = 1` via menu "O")
   - Separator: `", "` (comma space, line 929)
   - Format: `Dec2 temperature` (2 decimal places, line 1047)
   - Calculation (lines 1043-1046):
     ```
     tFloat1 = [ADC channel 1 voltage]
     temperature = (tFloat1 - 1.8639) / (-0.01177)
     ```
   - Units: Degrees Celsius

4. **Line Voltage (Vin)** (optional, if `LineVoutput = 1` via menu "O")
   - Separator: `", "` (comma space, line 933)
   - Format: `Dec3 tFloat1` (3 decimal places, line 1054)
   - Calculation (lines 1051-1053):
     ```
     tFloat1 = [ADC channel 0 voltage]
     tFloat1 = tFloat1 / 0.23
     ```
   - Units: Volts

5. **Line Terminator**: CR LF (`13, 10` or `0x0D 0x0A`, line 936)

#### Configuration for Optional Fields

- Set via menu option **"O"** (lines 1203-1231)
  - Temperature: Y/N prompt
  - Line Voltage: Y/N prompt

#### Decimal Format

- Standard ASCII decimal notation
- Negative sign prefix if value < 0
- No thousands separator
- Decimal point: `.` (period)

**No Checksum or CRC** in standard digital output mode.

---

## Deliverable 4: Edge Cases & Recovery

### 1. Sending "X" While Already Streaming vs While in Menu

#### Case A: "X" sent while streaming (freerun or polled mode, not in menu)

- **Result**: No effect. Character ignored.
- **Reason**: Main sampling loops (lines 530-542 freerun, 583-645 polled) do not parse characters directly. Only interrupt handler responds to ESC/`?` (lines 708, 791). The character `'X'` does not trigger interrupt handling.
- **Exception**: If device somehow enters the menu command parser during streaming (not expected in normal flow), it would be caught by `Case "A" To "Z"` (line 804) but not acted upon unless part of a multi-byte command.

#### Case B: "X" sent while in menu

- **Result**: Immediate exit → `"Rebooting program"` → MCU reset.
- **Reason**: Menu loop Select Case (line 1105) matches `"X"` → jumps to `EndMenu` → calls `Reset` (line 1589).

**Recovery**: After reset, device re-enters normal startup sequence. Mode determined by saved `OperatingMode` in EEPROM.

---

### 2. Sending Poll Command While in Freerun

**Command**: `><TAG>` sent while device is in freerun mode (OperatingMode="0")

**Result**: No effect. Data continues streaming.

**Reason**:
- Freerun mode uses interrupt handler `ComInterrupt2150` (line 787).
- Interrupt handler checks for `>` (line 800) and sets `FirstChar = ">"` (line 802).
- Next character checked if it matches `sTAG` (lines 804-810). If match, sets `Queried = 1`.
- However, freerun main loop (lines 530-542) **does not check `Queried` flag**. It continuously calls `GoSub Sendit` after each average completes (line 538), regardless of query state.
- The `Queried` flag is only used in polled mode's `While Queried = 0` blocking loop (line 626).

**Conclusion**: Poll command is harmless in freerun but has no effect. Stream continues uninterrupted.

---

### 3. Error Banners / "Invalid Selection" Text

| Error Condition | Verbatim Text | Context | Duration |
|-----------------|---------------|---------|----------|
| Averaging < 1 | `CR LF CR LF CR LF "****Invalid number, averaging set to 12. Command ignored ****" CR LF CR LF CR LF CR LF` | Menu option "A" | 4-second delay (line 1118) |
| Invalid ADC rate | `CR LF CR LF CR LF "Invalid rate!!! Command is ignored."` | Menu option "R" | 5-second delay (line 1458-1459) |
| ADC rate setup failed | `CR LF CR LF "**** Oh my goodness! Option ADC rate setting failed. Try again ****" CR LF` | Menu option "R" after valid input | 3-second delay (line 1465-1466) |
| Bad TAG (polled mode) | `" Bad TAG " CR LF` | Menu option "M" when setting TAG | No delay |
| Mode confused | `"I am confused" CR LF` | Menu option "M" if input != "0" or "1" | No delay |
| ADC startup failure | `"SetupADC did not complete correctly" CR LF` | Power-on / reset | Immediately jumps to ProcessMenu (line 469-472) |
| ADC not OK | `"ADC did not startup ok" CR LF` | Power-on / reset | Non-fatal, continues (line 478) |
| Timeout waiting for input | `"Timed out waiting for response. " CR LF CR LF` | Menu HRSIn timeout (20 seconds) | Jumps to Resu label (line 1593) |

#### Recovery from Errors

- Most errors print message, delay, then return to menu loop (no exit).
- Timeout errors (`Resu` label) print message and return to menu loop.
- Critical ADC failure forces immediate jump to `ProcessMenu` (gives user chance to fix via menu or inspect settings).

---

### 4. Timeouts After Sending A/R/M

#### Averaging (A)

- **Timeout**: None. `HRSIn Dec adcToAverage` (line 1114) blocks indefinitely until CR received.
- **If user never sends CR**: Device hangs waiting for input. No watchdog (WDT disabled, line 57).

#### Sampling Rate (R)

- **Timeout**: None. `HRSIn Dec br` (line 1446) blocks indefinitely.
- **Same risk**: Indefinite hang if no CR sent.

#### Mode (M)

- **Timeout**: **20 seconds** (20000 ms, line 1355, 1364).
- **On timeout**: Jumps to `Resu` label → prints `"Timed out waiting for response. " CR LF CR LF` → returns to menu loop (lines 1592-1593).
- **User can retry**: Menu redisplays after timeout.

#### Recommendation for Headless Controller

- Always send CR within 20 seconds for "M" command.
- For "A" and "R", ensure CR is sent. If no response after ~30 seconds, consider connection lost and reset device (power cycle or use watchdog if enabled in custom firmware).

---

### 5. "Press Key to Continue" Pauses in Menu

**Finding**: **None detected** in this firmware version.

#### Delays Present

- 1-second delay before menu display (`DelayMS 1000`, line 1092) — automatic, no user input required.
- Error message delays (4s for averaging error, 5s for rate error, 3s for ADC config failure) — automatic, no keypress needed.
- 100ms delay after power-on before signon banner (`DelayMS 100`, line 871).
- 2-second delay after "S" diagnostic display (`DelayMS 2000`, line 1475).

**No blocking "press any key" prompts found.**

---

### 6. Power-On Banner(s) and How to Skip to Stable State

#### Power-On Sequence (lines 452-510)

1. **Call `SignonMessage`** (line 459)
   - **Condition**: If `QuietMode = 1`, returns immediately (line 869) — **no banner**.
   - **Condition**: If `QuietMode = 0` (default), prints full multi-line banner (lines 872-920):
     ```
     CR LF "Biospherical Instruments Inc: Digital Engine Vers 4.003" CR LF
     "Unit ID <serial>" CR LF
     "Operating in <mode> mode" CR LF
     "ADC sample rate <rate>, gain <gain>" CR LF
     "Averaging <N> readings" CR LF
     [ADC buffer status]
     "Sensor temperature: <temp> C" CR LF
     "Input Supply Voltage: <voltage>v" CR LF
     "Calfactor: <CalFactor><units>" CR LF
     [LED status if applicable]
     ```

2. **ADC Initialization** (lines 464-480)
   - Prints `"ADC OK"` (if QuietMode=0 and init successful).
   - On failure: `"SetupADC did not complete correctly"` → jumps to `ProcessMenu` (forces user into menu to fix).

3. **Branch to Sampling Mode** (lines 502-507)
   - **Polled**: Prints (if QuietMode=0): `"Entering polled mainline sampling"` CR LF → silent waiting.
   - **Freerun**: Prints (if QuietMode=0): `"Start free run sampling"` CR LF `"Starting Sampling; quiet mode =0"` CR LF → begins streaming data.

#### How to Skip Power-On Banner

**Option 1: Set QuietMode = 1 (via menu option "Q")**

1. Navigate to menu (send ESC or `?`)
2. Send `Q` CR
3. Respond to prompt: `"Enter 1 for quiet mode with no sign-on message, 0 for sign-on message at power-up: "`
4. Send `1` CR
5. Exit menu (`X` CR)
6. **Result**: On next power-on/reset, no banner printed. Device immediately enters sampling mode after ADC init (which also becomes silent).

**Option 2: Ignore/Flush Banner (Software Approach)**

- After power-on, flush serial RX buffer for ~2 seconds.
- **Freerun**: Wait for first data line (starts with preamble or digit, ends with CR LF). Validate format.
- **Polled**: Send poll command `*<TAG>Q000!` then `><TAG>`. If response received (starts with TAG), device is ready.
- **Identify stable state**:
  - Freerun: Steady stream of data lines at expected rate.
  - Polled: Silent until queried, responds to `><TAG>` with single data line.

#### Timing to Stable State

- **QuietMode=0**: ~2-3 seconds (banner print + ADC init).
- **QuietMode=1**: ~500ms (ADC init only, minimal output).

#### Detecting Stable State Programmatically

- **Freerun**: After power-on, monitor serial input. Once 2-3 consecutive valid data lines received at expected interval, consider stable.
- **Polled**: After power-on, wait ~2 seconds, send `*<TAG>Q000!`, wait 500ms for averaging to start (depends on rate), send `><TAG>`. If valid response received (starts with `<TAG>,`), device is stable.

**Escape from Banner Flood**: If banner is printing and controller wants to enter menu immediately, send ESC or `?`. Firmware's interrupt handler triggers on receive (line 708, 791), regardless of current execution state. However, during power-on before interrupts are enabled (line 526 for freerun, implied in polled setup), characters may be missed. **Best practice**: Wait for banner to complete or flush RX buffer before sending commands.

---

### 7. Special Notes on Polled Mode Command Sequence

**CRITICAL Discovery**: Polled mode requires **two-step query process**:

#### Step 1: Start Averaging (Initialization Command)

- **Command**: `*<TAG>Q000!`
- **Breakdown** (lines 718-740):
  - `*` = Start character (line 720)
  - `<TAG>` = Matches sensor's stored TAG (line 723)
  - `Q` = Query start command (line 726-728)
  - `000` = Padding (ignored, lines 755-756)
  - `!` = Terminator (line 737)
- **Action**: Sets `cmd2100Requested = "Q"`, triggers `cmd2100ExecuteNow = 1`, which clears averages and sets `SamplingStarted = 1`, `Queried = 0` (lines 592-596).
- **Effect**: Device begins accumulating ADC readings into average. No output yet.
- **When to send**: Once after power-on or mode change. Not required for every query, but device must have `SamplingStarted = 1` to respond to data queries.

#### Step 2: Query Data (Repeated Per Sample)

- **Command**: `><TAG>`
- **Breakdown** (lines 800-810):
  - `>` = Poll start character (line 800, sets `FirstChar = ">"`)
  - `<TAG>` = Matches sensor's stored TAG (line 806)
- **Action**: Interrupt handler sets `Queried = 1` (line 807).
- **Effect**: Releases device from `While Queried = 0` blocking loop (line 626), triggers data transmission via `Polled2150Sending` (line 638), which calls `Sendit` (line 652).
- **Output**: Single data line: `<TAG>,<preamble><calibrated_value>[,<temp>][,<Vin>]` CR LF (line 650).
- **When to send**: After each desired sample. Device re-enters `While Queried = 0` loop after sending, waiting for next query.

#### Full Polled Workflow

```
1. Power-on (device enters polled mode silently if QuietMode=1)
2. Controller waits ~2 seconds for ADC init
3. Controller sends: *<TAG>Q000!  (no response expected)
4. Wait for averaging period: adcToAverage / ADC_rate seconds
   Example: 125 avg / 125 Hz = 1 second
5. Controller sends: ><TAG>
6. Device responds: <TAG>,123.456789 CR LF
7. Repeat steps 5-6 for each sample
```

#### Error if Step 1 Skipped

- If controller sends `><TAG>` without first sending `*<TAG>Q000!`, device is stuck in `While 1=1` loop with `SamplingStarted = 0`.
- Average never accumulates (`If SamplingStarted = 1` check fails, line 618).
- `While Queried = 0` loop (line 626) never entered (because `Averaged` never equals `adcToAverage`, line 622).
- **Result**: No response to `><TAG>`. Device appears to hang.

**Recovery**: Send `*<TAG>Q000!` to initialize, then retry `><TAG>`.

---

### 8. Re-entering Menu: State Preservation

**Observation**: Entering menu via ESC/`?` **disables interrupts** (lines 712-713, 795-796) and **does not preserve averaging state**.

**Consequence**: After exiting menu with `X` (which triggers full reset), device:

1. Resets all variables to power-on defaults.
2. Reloads EEPROM settings (OperatingMode, TAG, adcToAverage, etc.).
3. Reinitializes ADC.
4. Restarts sampling from scratch.

**For Polled Mode**: After menu exit, controller must resend `*<TAG>Q000!` to restart averaging.

**For Freerun Mode**: After menu exit, stream resumes automatically (no command needed), but any in-progress average is discarded.

---

### 9. Handling "^" (Configuration String Dump)

- **Command**: `^` (caret)
- **Context**: Menu only (line 1107-1108)
- **Action**: Calls `SendAllParameters` (line 1668-1689)
- **Output**: Single CSV line with all settings:

```
<adcToAverage>,<baudrate>,<CalFactor>,<Description>,E,<Version>,G,H,<Serial>,<Immersion>,<DarkValue>,<SupplyVoltage>,<OperatingMode>,<sTAG>,<Preamble>,<TempOutput>,<adcRate>,S,<temp_C>,<Units>,V,<CalibrationMode>...
```

- **Fields**: 21+ comma-separated values
- **Example** (reconstructed from lines 1670-1689):

```
125,9600,1.234567,QSP,E,4.003,G,H,Q12345,1.000000,0.005000,12.345,1,A,0,125,S,V,A
```

- **Use**: Snapshot of all configuration parameters for logging or backup.
- **Does not exit menu**: After printing, returns to menu loop.

---

### 10. Watchdog and Hang Recovery

- **Watchdog**: Disabled (`Declare Watchdog = off`, line 45; `WDT = OFF`, line 57).
- **Implication**: If firmware hangs (e.g., indefinite `HRSIn` wait, logic error), no automatic recovery.
- **User Recovery**: Power cycle device or reset via external hardware reset pin.
- **For Production**: Consider enabling watchdog in custom firmware or implementing external hardware watchdog circuit.

---

## Summary: Quick Reference for Headless Controller

### Initialization Sequence (Power-On)

1. **Wait 2-3 seconds** for power-on banner and ADC init (longer if QuietMode=0).
2. **Flush RX buffer** (discard banner text).
3. **Detect mode**:
   - If data lines start arriving → Freerun mode.
   - If silent → Polled mode.

### Entering Menu

- **Send**: `0x1B` (ESC) or `?`
- **Wait for**: `"Select the letter of the menu entry:"` prompt.

### Setting Averaging

- **Send**: `A` CR
- **Wait for**: `"Enter # readings to average before update (1-65535): "`
- **Send**: `<number>` CR (e.g., `125` CR)
- **Wait for**: `"ADC set to averaging <number>"`

### Setting Sampling Rate

- **Send**: `R` CR
- **Wait for**: `"Enter ADC rate..."` prompt
- **Send**: `<rate>` CR (e.g., `125` CR)
- **Wait for**: `"ADC rate set to <rate>"`

### Setting Mode to Polled

- **Send**: `M` CR
- **Wait for**: `"Enter the operating mode number: "`
- **Send**: `1` CR
- **Wait for**: `"Enter the single character that will be the tag..."`
- **Send**: `<TAG>` CR (e.g., `A` CR)

### Setting Mode to Freerun

- **Send**: `M` CR
- **Wait for**: `"Enter the operating mode number: "`
- **Send**: `0` CR
- **Wait for**: Device echoes `0` CR LF

### Exiting Menu

- **Send**: `X` CR
- **Expect**: `"Rebooting program"` → device resets
- **Wait 2 seconds** for restart.

### Polled Mode Operation

1. **After power-on or mode change**:
   - **Send**: `*<TAG>Q000!` (e.g., `*AQ000!`)
   - **No response expected**.
2. **Wait** averaging period (adcToAverage / ADC_rate seconds).
3. **Query sample**:
   - **Send**: `><TAG>` (e.g., `>A`)
   - **Receive**: `<TAG>,<data>` CR LF (e.g., `A,123.456789` CR LF)
4. **Repeat step 3** for each sample.

### Freerun Mode Operation

- **After exit from menu**: Device automatically streams data.
- **No commands needed**.
- **Parse lines**: `<preamble><value>[,<temp>][,<Vin>]` CR LF
- **Pause**: Send ESC or `?` to enter menu.
- **Resume**: Send `X` CR in menu (triggers reboot, restarts stream).

### Error Handling

- **Invalid input errors**: Device prints message, returns to menu. Retry command.
- **Timeout errors (M command)**: Device prints timeout message, returns to menu. Retry.
- **No response in polled mode**: Resend `*<TAG>Q000!`, wait, then retry `><TAG>`.
- **Hung device**: Power cycle (no watchdog).

---

**End of Documentation**
