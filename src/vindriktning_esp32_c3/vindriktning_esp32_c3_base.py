from enum import Enum
import logging
from typing import List

from faebryk.core.core import Module
from faebryk.core.util import get_all_nodes
from faebryk.library.Constant import Constant
from faebryk.library.DifferentialPair import DifferentialPair
from faebryk.library.Electrical import Electrical
from faebryk.library.ElectricLogic import ElectricLogic
from faebryk.library.ElectricPower import ElectricPower
from faebryk.library.LED import LED
from faebryk.library.LEDIndicator import LEDIndicator
from faebryk.library.PoweredLED import PoweredLED
from faebryk.library.PowerSwitch import PowerSwitch
from faebryk.library.Range import Range
from faebryk.library.UART_Base import UART_Base
from vindriktning_esp32_c3.library.USB_C_PSU_Vertical import USB_C_PSU_Vertical
from faebryk.library.Capacitor import Capacitor
from faebryk.library.TBD import TBD
from faebryk.libs.units import k, m, n, u
from faebryk.libs.util import times
from vindriktning_esp32_c3.library.ME6211C33M5G_N import ME6211C33M5G_N
from vindriktning_esp32_c3.library.B4B_ZR_SM4_TF import B4B_ZR_SM4_TF
from vindriktning_esp32_c3.library.pf_74AHCT2G125 import pf_74AHCT2G125
from vindriktning_esp32_c3.library.pf_533984002 import pf_533984002
from vindriktning_esp32_c3.library.ESP32_C3_MINI_1 import ESP32_C3_MINI_1
from vindriktning_esp32_c3.library.HLK_LD2410B_P import HLK_LD2410B_P
from vindriktning_esp32_c3.library.XL_3528RGBW_WS2812B import XL_3528RGBW_WS2812B
from vindriktning_esp32_c3.picker import pick_component
from faebryk.library.can_bridge_defined import can_bridge_defined
from faebryk.core.util import connect_to_all_interfaces
from faebryk.library.has_defined_type_description import has_defined_type_description
from faebryk.library.MOSFET import MOSFET
from faebryk.library.Electrical import Electrical
from faebryk.core.core import LinkDirect

logger = logging.getLogger(__name__)


class Ikea_Vindriktning_PM_Sensor(Module):
    """
    Module containing the hardware needed to connect to the fan and PM sensor
    in the IKEA VINDRIKTNING
      - Controllable FAN
      - Level shifted UART
      - Fan LED indicator
    """

    def __init__(self) -> None:
        super().__init__()

        # interfaces
        class _IFs(Module.IFS()):
            power_5v_in = ElectricPower()
            power_3v3_in = ElectricPower()
            fan_enable = ElectricLogic()
            uart = UART_Base()

        self.IFs = _IFs(self)

        # components
        class _NODEs(Module.NODES()):
            fan_indicator = LEDIndicator(logic_low=False, normally_on=False)
            fan_connector = pf_533984002()
            pm_sensor_connector = B4B_ZR_SM4_TF()
            fan_power_switch = PowerSwitch(lowside=True, normally_closed=False)
            pm_sernsor_buffer = times(2, LevelBuffer)  # 0=tx, 1=rx

        self.NODEs = _NODEs(self)

        # aliasses
        tx = 0
        rx = 1

        # TODO ^that or this?
        class RxTx(Enum):
            TX = 0
            RX = 1

        gnd = self.IFs.power_5v_in.NODEs.lv
        v_5V = self.IFs.power_5v_in.NODEs.hv
        v_3V3 = self.IFs.power_3v3_in.NODEs.hv

        # make internal connections
        # fan connector
        self.NODEs.fan_connector.IFs.pin[0].connect_via(
            self.NODEs.fan_power_switch, gnd
        )
        self.NODEs.fan_connector.IFs.pin[1].connect(v_5V)
        self.NODEs.fan_power_switch.IFs.logic_in.connect(self.IFs.fan_enable)

        # fan indicator (on 3v3 power)
        self.NODEs.fan_indicator.IFs.logic_in.connect(self.IFs.fan_enable)
        self.NODEs.fan_indicator.IFs.power_in.connect(self.IFs.power_3v3_in)

        # tx buffer to 5v power (mcu 3v3 > sensor 5v)
        self.NODEs.pm_sernsor_buffer[tx].IFs.power.connect(self.IFs.power_5v_in)
        # rx buffer to 3v3 power (mcu 3v3 < sensor 5v)
        self.NODEs.pm_sernsor_buffer[RxTx.RX.value].IFs.power.connect(
            self.IFs.power_3v3_in
        )

        # pm sensor connector to buffers
        self.NODEs.pm_sensor_connector.IFs.pin[3].connect(gnd)
        self.NODEs.pm_sensor_connector.IFs.pin[2].connect(v_5V)
        self.NODEs.pm_sensor_connector.IFs.pin[1].connect_via(
            self.NODEs.pm_sernsor_buffer[rx],
            self.IFs.uart.NODEs.rx.NODEs.signal,
        )
        self.NODEs.pm_sensor_connector.IFs.pin[0].connect_via(
            self.NODEs.pm_sernsor_buffer[RxTx.TX.value],
            self.IFs.uart.NODEs.tx.NODEs.signal,
        )


class LevelBuffer(Module):
    """
    Logic buffer using a 74HCT1G125 single gate buffer
      - Enable pin active by default
    """

    def __init__(self) -> None:
        super().__init__()

        class _IFs(Module.IFS()):
            logic_in = ElectricLogic()
            logic_out = ElectricLogic()
            power = ElectricPower()

        self.IFs = _IFs(self)

        class _NODEs(Module.NODES()):
            buffer = pf_74AHCT2G125()
            decoupling_cap = Capacitor(Constant(100 * n))

        self.NODEs = _NODEs(self)

        # connect power
        self.IFs.power.connect(self.NODEs.buffer.IFs.power)

        # connect decouple capacitor
        self.IFs.power.decouple(self.NODEs.decoupling_cap)

        # connect enable pin to power.lv to always enable the buffer
        self.NODEs.buffer.IFs.oe.NODEs.signal.connect(self.IFs.power.NODEs.lv)

        # Add bridge trait
        self.add_trait(can_bridge_defined(self.IFs.logic_in, self.IFs.logic_out))


class digitalLED(Module):
    """
    Create a string of WS2812B RGBW LEDs with optional signal level translator
    """

    def __init__(self, pixels: int, buffered: bool = True) -> None:
        super().__init__()

        self.pixels = pixels
        self.buffered = buffered

        class _IFs(Module.IFS()):
            data_in = ElectricLogic()
            power = ElectricPower()

        self.IFs = _IFs(self)

        class _NODEs(Module.NODES()):
            if buffered:
                buffer = LevelBuffer()
            leds = times(pixels, XL_3528RGBW_WS2812B)
            # decoupling cap for every LED
            # decoupling_cap = times(pixels, Capacitor(capacitance=TBD()))
            decoupling_cap_led = []
            for _ in range(pixels):
                decoupling_cap_led.append(Capacitor(Constant(100 * n)))

        self.NODEs = _NODEs(self)

        # add a decoupling cap for every LED to the LED power rail
        for _ in range(pixels):
            self.IFs.power.decouple(self.NODEs.decoupling_cap_led[_])
            # connect power
            self.IFs.power.connect(self.NODEs.leds[_].IFs.power)

        # connect all LEDs in series
        for _ in range(pixels - 1):
            self.NODEs.leds[_].IFs.do.connect(self.NODEs.leds[_ + 1].IFs.di)

        # connect buffer to the 1st LED
        if buffered:
            # self.IFs.data_in.connect(self.NODEs.buffer.IFs.logic_in[0])
            # self.NODEs.buffer.IFs.logic_out[0].connect(self.NODEs.leds[0].IFs.di)
            self.IFs.data_in.connect_via(self.NODEs.buffer, self.NODEs.leds[0].IFs.di)
        else:
            self.IFs.data_in.connect(self.NODEs.leds[0].IFs.di)


class Vindriktning_ESP32_C3(Module):
    def __init__(self) -> None:
        super().__init__()

        # interfaces
        class _IFs(Module.IFS()):
            pass

        self.IFs = _IFs(self)

        # components
        class _NODEs(Module.NODES()):
            pm_sensor = Ikea_Vindriktning_PM_Sensor()
            leds = digitalLED(5, buffered=True)
            mcu = ESP32_C3_MINI_1()
            pressence_sensor = HLK_LD2410B_P()
            psu = USB_C_PSU_Vertical()
            ldo = ME6211C33M5G_N()

        self.NODEs = _NODEs(self)

        # connections
        # power
        connect_to_all_interfaces(
            self.NODEs.ldo.IFs.power_out,
            [
                self.NODEs.mcu.IFs.pwr3v3,
                self.NODEs.pm_sensor.IFs.power_3v3_in,
            ],
        )
        connect_to_all_interfaces(
            self.NODEs.psu.IFs.power_out,
            [
                self.NODEs.ldo.IFs.power_in,
                self.NODEs.pressence_sensor.IFs.power,
                self.NODEs.leds.IFs.power,
                self.NODEs.pm_sensor.IFs.power_5v_in,
            ],
        )

        # sensors
        self.NODEs.pressence_sensor.IFs.uart.connect(self.NODEs.mcu.IFs.serial)
        self.NODEs.pressence_sensor.IFs.out.connect(self.NODEs.mcu.IFs.gpio[6])
        self.NODEs.pm_sensor.IFs.uart.NODEs.rx.connect(self.NODEs.mcu.IFs.gpio[8])
        self.NODEs.pm_sensor.IFs.uart.NODEs.tx.connect(self.NODEs.mcu.IFs.gpio[9])
        self.NODEs.pm_sensor.IFs.fan_enable.connect(self.NODEs.mcu.IFs.gpio[7])

        # LEDs
        self.NODEs.leds.IFs.data_in.connect(self.NODEs.mcu.IFs.gpio[5])

        # function

        # fill parameters
        cmps = get_all_nodes(self)
        for cmp in cmps:
            # logger.warn(f"{str(cmp.get_full_name).split('|')[2].split('>')[0]}")
            if isinstance(cmp, PowerSwitch):
                powerswitch = cmp
                powerswitch.NODEs.pull_resistor.set_resistance(Constant(100 * k))
            if isinstance(cmp, Capacitor):
                capacitor = cmp
                if isinstance(capacitor.capacitance, TBD):
                    logger.warn(
                        f"Found capacitor with TBD value at {capacitor.get_full_name}"
                    )
                    capacitor.set_capacitance(Constant(100 * n))
            if isinstance(cmp, PoweredLED):
                cmp.NODEs.led.set_forward_parameters(
                    voltage_V=Constant(2), current_A=Constant(10 * m)
                )
                R_led = cmp.NODEs.led.get_trait(
                    LED.has_calculatable_needed_series_resistance
                ).get_needed_series_resistance_ohm(5)
                # Take higher resistance for dimmer LED
                R_led_dim = Range(R_led.value * 2, R_led.value * 4)
                cmp.NODEs.current_limiting_resistor.set_resistance(R_led_dim)
            if isinstance(cmp, LED):
                cmp.add_trait(has_defined_type_description("D"))
            if isinstance(cmp, MOSFET):
                cmp.add_trait(has_defined_type_description("Q"))

        # footprints
        for cmp in cmps:
            if not isinstance(cmp, Module):
                continue
            pick_component(cmp)

        # Check for electrical connections util
        def get_connections(mif: Electrical):
            return [
                other
                for link in mif.GIFs.connected.connections
                for other in link.get_connections()
                if isinstance(link, LinkDirect)
                and other is not mif.GIFs.connected
                and isinstance(other.node, Electrical)
            ]

        # easy ERC
        for cmp in cmps:
            if not isinstance(cmp, Module):
                continue
            for interface in cmp.IFs.get_all():
                if isinstance(interface, Electrical):
                    if not get_connections(interface):
                        logger.warn(f"{interface} is not connected!")
