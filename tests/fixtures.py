"""ipmitool output in the format of an X9SRL-F (sample values)."""

SENSOR = """\
CPU Temp         | 42.000     | degrees C  | ok    | 0.000     | 0.000     | 0.000     | 80.000    | 85.000    | 85.000
System Temp      | 31.000     | degrees C  | ok    | -9.000    | -7.000    | -5.000    | 80.000    | 85.000    | 90.000
Peripheral Temp  | 47.000     | degrees C  | nc    | -9.000    | -7.000    | -5.000    | 45.000    | 85.000    | 90.000
FAN1             | 1500.000   | RPM        | ok    | 300.000   | 450.000   | 600.000   | 18975.000 | 19050.000 | 19125.000
FAN2             | 150.000    | RPM        | cr    | 300.000   | 450.000   | 600.000   | 18975.000 | 19050.000 | 19125.000
FAN3             | na         |            | na    | na        | na        | na        | na        | na        | na
VCPU             | 0.856      | Volts      | ok    | 0.480     | 0.512     | 0.544     | 1.488     | 1.520     | 1.552
12V              | 12.137     | Volts      | ok    | 10.144    | 10.272    | 10.784    | 12.960    | 13.280    | 13.408
VBAT             | 3.120      | Volts      | ok    | 2.400     | 2.544     | 2.832     | 3.408     | 3.552     | 3.696
Chassis Intru    | 0x0        | discrete   | 0x0000| na        | na        | na        | na        | na        | na
"""

CHASSIS = """\
System Power         : on
Power Overload       : false
Power Interlock      : inactive
Main Power Fault     : false
Power Control Fault  : false
Power Restore Policy : always-off
Last Power Event     : command
Chassis Intrusion    : inactive
Front-Panel Lockout  : inactive
Drive Fault          : false
Cooling/Fan Fault    : false
"""

SEL = """\
   1 | 10/27/2026 | 08:12:44 | Power Supply #0xc8 | Failure detected | Asserted
   2 | 10/27/2026 | 08:12:50 | Fan #0x42 | Lower Critical going low  | Asserted | Reading 150 < Threshold 450 RPM
   a | Pre-Init  |  Time-stamp Not Available | Chassis Intru #0xaa | General Chassis intrusion | Deasserted
"""

MC_INFO = """\
Device ID                 : 32
Device Revision           : 1
Firmware Revision         : 3.19
IPMI Version              : 2.0
Manufacturer ID           : 10876
Manufacturer Name         : Supermicro
Product ID                : 1572 (0x0624)
"""

FRU = """\
FRU Device Description : Builtin FRU Device (ID 0)
 Board Mfg Date        : Mon Jan  1 00:00:00 1996
 Board Mfg             : Supermicro
 Board Product         : X9SRL-F
 Board Serial          : ZM12345678
 Product Name          : SYS-5017R
 Product Serial        : S123456
"""
