#  Skinetic SDK
#  Copyright (C) 2023 Actronika SAS
#      Author: Sylvain Gaultier <sylvain.gaultier@actronika.com>

# This is a python3 interface for a C shared library.
# The library binary suited for your platform must be located in the same folder
# as this script, with the following name:
# Windows: SkineticSDK.dll
# Linux: libSkineticSDK.so
# Darwin: libSkineticSDK.dylib

from enum import Enum, IntEnum
from ctypes import *
from os.path import dirname, abspath, join
import time
import typing
import platform

_DEV_TYPE_PRODUCT_TYPE_WEARABLE = 0x01
_DEV_TYPE_PRODUCT_TYPE_DEVKIT = 0xFF
_DEV_TYPE_ZONE_TORSO = 0x01
_DEV_TYPE_ZONE_UNKNOWN = 0xFF
_USB_VID = 0x34A8
_USB_PID_SKINETIC = 0x0110
_USB_PID_HSDMK2 = 0x0122
_USB_PID_HSDMK3 = 0x0123
_DEV_TYPE_SKINETIC = ((_USB_PID_SKINETIC << 16) | (_DEV_TYPE_PRODUCT_TYPE_WEARABLE << 8)
                      | _DEV_TYPE_ZONE_TORSO)
_DEV_TYPE_HSDMK2 = ((_USB_PID_HSDMK2 << 16) | (_DEV_TYPE_PRODUCT_TYPE_DEVKIT << 8)
                    | _DEV_TYPE_ZONE_UNKNOWN)
_DEV_TYPE_HSDMK3 = ((_USB_PID_HSDMK3 << 16) | (_DEV_TYPE_PRODUCT_TYPE_DEVKIT << 8)
                    | _DEV_TYPE_ZONE_UNKNOWN)


class SkineticErrorCode(IntEnum):
    """
    Error codes returned by the functions of the library.
    """
    ## No error
    eNoError = 0
    ## Other
    eOther = -1
    ## Invalid Parameter
    eInvalidParam = -2
    ## No device connected
    eNotConnected = -3
    ## Output is not supported on this platform
    eOutputNotSupported = -4
    ## Invalid json
    eInvalidJson = -5
    ## Device not reachable
    eDeviceNotReachable = -6
    ## A priority command is waiting to be processed
    ePendingCommand = -7
    ## No available slot on the board
    eNoMoreSlot = -8
    ## No Skinetic instance created
    eNoInstance = -9
    ## Received an invalid message
    eInvalidMessage = -10
    ## Process is already running
    eAlreadyRunning = -11
    ## A device is already connected
    eDeviceAlreadyConnected = -12
    ## The initialization of the device has been interrupted
    eInitializationInterrupted = -13
    ## Play was ignored due to overall trigger strategy
    ePlayIgnored = -14
    ## PortAudio raised an error
    eErrorPortAudio = -15
    ## An error happened with the socket
    eSocketError = -16
    ## ASH-fx library raised an error
    eAshError = -17
    ## Core Error: Invalid argument
    eCoreInvalidArgument = -100
    ## Core Error: Invalid spn
    eCoreInvalidSpn = -99
    ## Core Error: Invalid layout
    eCoreInvalidLayout = -98
    ## Core Error: ID already allocated
    eCoreAlreadyAllocated = -97
    ## Core Error: Invalid sequence ID
    eCoreSequenceNotAllocated = -96
    ## Core Error: Invalid pattern ID
    eCorePatternNotAllocated = -95
    ## Core Error: Pattern in use
    eCorePatternInUse = -94
    ## Core Error: Sequence already set to play
    eCoreSequenceAlreadyPlaying = -93
    ## Core Error: Invalid Operation
    eCoreInvalidOperation = -92


def _handle_error_code(code: int) -> int:
    if code >= 0:
        return code
    else:
        try:
            msg = "ski_error_t::" + SkineticErrorCode(code).name
        except ValueError:
            raise RuntimeError("Unknown error code: " + str(code))
    if code == SkineticErrorCode.eOther:
        raise RuntimeError(msg)
    elif code == SkineticErrorCode.eInvalidParam:
        raise ValueError(msg)
    elif code == SkineticErrorCode.eNotConnected:
        raise ConnectionError(msg)
    elif code == SkineticErrorCode.eOutputNotSupported:
        raise ValueError(msg)
    elif code == SkineticErrorCode.eInvalidJson:
        raise ValueError(msg)
    elif code == SkineticErrorCode.eDeviceNotReachable:
        raise ConnectionError(msg)
    elif code == SkineticErrorCode.ePendingCommand:
        raise RuntimeError(msg)
    elif code == SkineticErrorCode.eNoMoreSlot:
        raise RuntimeError(msg)
    elif code == SkineticErrorCode.eNoInstance:
        raise RuntimeError(msg)
    elif code == SkineticErrorCode.eInvalidMessage:
        raise ValueError(msg)
    elif code == SkineticErrorCode.eAlreadyRunning:
        raise RuntimeError(msg)
    elif code == SkineticErrorCode.eDeviceAlreadyConnected:
        raise ConnectionError(msg)
    elif code == SkineticErrorCode.eInitializationInterrupted:
        raise RuntimeError(msg)
    elif code == SkineticErrorCode.ePlayIgnored:
        raise RuntimeError(msg)
    elif code == SkineticErrorCode.eErrorPortAudio:
        raise RuntimeError(msg)
    elif code == SkineticErrorCode.eAshError:
        raise RuntimeError(msg)
    elif code == SkineticErrorCode.eSocketError:
        raise ConnectionError(msg)
    elif code == SkineticErrorCode.eCoreInvalidArgument:
        raise ValueError(msg)
    elif code == SkineticErrorCode.eCoreInvalidLayout:
        raise ValueError(msg)
    elif code == SkineticErrorCode.eCoreInvalidSpn:
        raise ValueError(msg)
    elif code == SkineticErrorCode.eCoreAlreadyAllocated:
        raise RuntimeError(msg)
    elif code == SkineticErrorCode.eCoreSequenceNotAllocated:
        raise RuntimeError(msg)
    elif code == SkineticErrorCode.eCorePatternNotAllocated:
        raise RuntimeError(msg)
    elif code == SkineticErrorCode.eCorePatternInUse:
        raise RuntimeError(msg)
    elif code == SkineticErrorCode.eCoreSequenceAlreadyPlaying:
        raise RuntimeError(msg)
    elif code == SkineticErrorCode.eCoreInvalidOperation:
        raise RuntimeError(msg)
    else:
        raise RuntimeError(msg)


class _DeviceInfo(Structure):
    # The attribute _fields_ is set at the end of the file because of circular
    # dependencies
    pass


class _CAudioSettings(Structure):
    _fields_ = [("device_name", c_char_p),
                ("audio_api", c_char_p),
                ("sample_rate", c_uint),
                ("buffer_size", c_uint),
                ("nb_stream_channel", c_int),
                ("suggested_latency", c_float)]


class _CEffectProperties(Structure):
    _fields_ = [("priority", c_int),
                ("volume", c_float),
                ("speed", c_float),
                ("repeat_count", c_int),
                ("repeat_delay", c_float),
                ("play_at_time", c_float),
                ("max_duration", c_float),
                ("effect_boost", c_int),
                ("override_pattern_boost", c_bool),
                ("height", c_float),
                ("tilting", c_float),
                ("heading", c_float),
                ("front_back_inversion", c_bool),
                ("up_down_inversion", c_bool),
                ("right_left_inversion", c_bool),
                ("front_back_addition", c_bool),
                ("up_down_addition", c_bool),
                ("right_left_addition", c_bool)]


class Skinetic:
    """
    This class is used as the main API interface
    """

    class DeviceScanInProgress(RuntimeError):
        """ @private
        """
        pass

    class LogLevel(Enum):
        """ Enum Level of logs
        """

        Trace = 0
        Debug = 1
        Info = 2
        Warn = 3
        Err = 4
        Critical = 5
        Off = 6

        @classmethod
        def from_param(cls, obj):
            """ @private
            """
            if not isinstance(obj, cls):
                raise TypeError
            return obj.value

    class ConnectionState(Enum):
        """ Enum describing device connection status
        """

        ## Device connection was broken, trying to reconnect
        Reconnecting = 3
        ## Device is disconnecting, releasing all resources
        Disconnecting = 2
        ## Connection to the device is being established, connection routine is active
        Connecting = 1
        ## Device is connected
        Connected = 0
        ## Device is disconnected
        Disconnected = -1

        @classmethod
        def from_param(cls, obj):
            """ @private
            """
            if not isinstance(obj, cls):
                raise TypeError
            return obj.value

    class EffectProperties:
        """ Haptic Structure to describe how effect instances reproduce
        a pattern with variations.

        The spatialization properties (height, heading and tilting) allows to apply
        the pattern on the haptic device at a different location by
        translating/rotating it or performing some inversion/addition.
        Notice that combining additions greatly increase the processing time of the
        transformation. If the pattern possesses too many shapes and keys, a
        perceptible delay might be induced.

        The three transformations are applied in this order: tilting, vertical rotation, vertical translation. The
        default position of a pattern is the one obtained when these three parameters are set to zero. The actual use
        of these 3 parameters depends on the default position of the pattern and the targeted interaction: e.g.; for
        a piercing shot, a heading between [-180; 180]° can be combined with a tilting between [-90; 90] when using a
        shape-based pattern centered in the middle of the torso; for a environmental effect, a heading between [-90;
        90]° (or [-180; 180]°) can be combined with a tilting between [-180; 180]° (resp. [-180; 0]°) when using a
        pattern with shapes centered on the top, etc. There are no actual bounds to the angles as not to restrict the
        usage. Notice that actuator-based patterns cannot be transformed in this version.

        The global boost intensity is applied to every effect being rendered as to increase them evenly. However,
        some effects are by design stronger than others. Hence, they all have a default boost value in the .spn that
        is added to the global boost intensity, and which can be set to compensate the discrepancy of intensity
        across a set of patterns. Weaker effects can have a high default boost value while, already strong effects
        can have a negative default boost value as to prevent the global boost intensity set by the user to increase
        the perceived intensity too much. Note that the resulting boost value is clamp between 0 and 100. When an
        instance of an effect is being rendered, the default boost value of the pattern, the one set in the design
        process, is used. If the boolean overridePatternBoost is set to true, the passed value effectBoost is used
        instead of the default one.

        Since all effects cannot be rendered simultaneously, the least priority ones are muted until
        the more priority ones are stopped of finished rendering. Muted effects are still running,
        but not rendered.

        The priority order is obtain using the priority level: priority increase from 10 to 1. In case
        of equality, the number of required simultaneous samples is used to determine which effect has the highest
        priority: effects using less simultaneous samples have a higher priority. Again, if the number of required
        simultaneous samples is the same, the most recent effect has a higher priority.
        """

        def __init__(self, priority: int = 5, volume: float = 100.0, speed: float = 1.0,
                     repeat_count: int = 1, repeat_delay: float = 0.0, play_at_time: float = 0.0,
                     max_duration: float = 0, effect_boost: int = 0, override_boost: bool = False,
                     height: float = 0.0, tilting: float = 0.0, heading: float = 0.0,
                     front_back_inversion: bool = False,
                     up_down_inversion: bool = False, right_left_inversion: bool = False,
                     front_back_addition: bool = False,
                     up_down_addition: bool = False, right_left_addition: bool = False):
            ## Level of priority [1; 10] (default - 5). In case too many effects are playing simultaneously,
            # the effect with the lowest priority (10) will be muted.
            self.priority = priority
            ## Percentage of the base volume between [0; 250]% (default - 100): [0;100[% the pattern attenuated,
            # 100% the pattern's base volume is preserved, ]100; 250]% the pattern is amplified.
            # Too much amplification may lead to the clipping of the haptic effects, distorting them
            # and producing audible artifacts
            self.volume = volume
            ## Time scale between [0.01; 100] (default - 1): [0.01; 1[ the pattern is slowed down, 1 the pattern
            # timing is preserved, ]1; 100] the pattern is accelerated. The resulting speed between
            # the haptic effect's and the samples' speed within the pattern cannot exceed these
            # bounds. Slowing down or accelerating a sample too much may result in a haptically poor
            # effect.
            self.speed = speed
            ## Number of repetition of the pattern (default - 1) if the max_duration is not reached.
            # If set to 0, the pattern is repeat indefinitely until it is either stopped with stop_effect()
            # or reach the maxDuration value.
            self.repeat_count = repeat_count
            ## Pause in second between to repetition of the pattern (default - 0). This value is not
            # affected by the speed parameter.
            self.repeat_delay = repeat_delay
            ## Time in the pattern at which the effect start to play (default - 0). This value need to be
            # lower than the max_duration. It also takes into account the repeat_count and the
            # repeat_delay of the pattern.
            self.play_at_time = play_at_time
            ## Maximum duration of the effect (default - 0), it is automatically stopped if the duration
            # is reached without any regards for the actual state of the repeat_count. A max_duration
            # of 0 remove the duration limit, making the effect ables to play indefinitely.
            self.max_duration = max_duration
            ## Boost intensity level percent [-100; 100] (default - 0) of the effect to use instead of the
            # default pattern value if override_boost is set to true. By using a negative value, can decrease or
            # even nullify the global intensity boost set by the user.
            self.effect_boost = effect_boost
            ## By setting this boolean to true (default - false), the effect will use the
            # effect_boost value instead of the default pattern value.
            self.override_boost = override_boost
            ## Normalized height [-1; 1] to translate the pattern by (default - 0). A positive value translate
            # the pattern upwards. Not applicable to actuator-based patterns.
            self.height = height
            ## Heading angle (in degree) to rotate the pattern by in the horizontal plan (default - 0). A positive
            # value rotates the pattern to the left of the vest. Not applicable to actuator-based patterns.
            self.tilting = tilting
            ## Tilting angle (in degree) to rotate the pattern by in the sagittal plan (default - 0). A positive
            # value rotates the pattern upwards from front to back. Not applicable to actuator-based patterns.
            self.heading = heading
            ## Invert the direction of the pattern on the front-back axis (default - false). Can be combined with other
            # inversion or addition. Not applicable to actuator-based patterns.
            self.front_back_inversion = front_back_inversion
            ## Invert the direction of the pattern on the up-down axis (default - false). Can be combined with other
            # inversion or addition. Not applicable to actuator-based patterns.
            self.up_down_inversion = up_down_inversion
            ## Invert the direction of the pattern on the right-left axis (default - false). Can be combined with other
            # inversion or addition. Not applicable to actuator-based patterns.
            self.right_left_inversion = right_left_inversion
            ## Perform a front-back addition of the pattern on the front-back axis (default - false). Overrides the
            # front_back_inversion. Can be combined with other inversion or addition. Not applicable to actuator-based patterns.
            self.front_back_addition = front_back_addition
            ## Perform an up-down addition of the pattern on the front-back axis (default - false). Overrides the
            # up_down_inversion. Can be combined with other inversion or addition. Not applicable to actuator-based patterns.
            self.up_down_addition = up_down_addition
            ## Perform a right-left addition of the pattern on the front-back axis (default - false). Overrides the
            # right_left_inversion. Can be combined with other inversion or addition. Not applicable to actuator-based patterns.
            self.right_left_addition = right_left_addition

        def set_values_from_cstruct(self, args: _CEffectProperties):
            """ @private
            """
            self.priority = args.priority
            self.volume = args.volume
            self.speed = args.speed
            self.repeat_count = args.repeat_count
            self.repeat_delay = args.repeat_delay
            self.play_at_time = args.play_at_time
            self.max_duration = args.max_duration
            self.effect_boost = args.effect_boost
            self.override_boost = args.override_pattern_boost
            self.height = args.height
            self.tilting = args.tilting
            self.heading = args.heading
            self.front_back_inversion = args.front_back_inversion
            self.up_down_inversion = args.up_down_inversion
            self.right_left_inversion = args.right_left_inversion
            self.front_back_addition = args.front_back_addition
            self.up_down_addition = args.up_down_addition
            self.right_left_addition = args.right_left_addition

        def to_c_type(self) -> _CEffectProperties:
            """ @private
            """
            new_class = _CEffectProperties()
            new_class.priority = self.priority
            new_class.volume = self.volume
            new_class.speed = self.speed
            new_class.repeat_count = self.repeat_count
            new_class.repeat_delay = self.repeat_delay
            new_class.play_at_time = self.play_at_time
            new_class.max_duration = self.max_duration
            new_class.effect_boost = self.effect_boost
            new_class.override_pattern_boost = self.override_boost
            new_class.height = self.height
            new_class.tilting = self.tilting
            new_class.heading = self.heading
            new_class.front_back_inversion = self.front_back_inversion
            new_class.up_down_inversion = self.up_down_inversion
            new_class.right_left_inversion = self.right_left_inversion
            new_class.front_back_addition = self.front_back_addition
            new_class.up_down_addition = self.up_down_addition
            new_class.right_left_addition = self.right_left_addition
            return new_class

    class ExpAudioPreset(Enum):
        """ Experimental - Preset of audio devices.
        eCustomDevice is to be used for a custom configuration.
        """
        ## Audio stream with a custom configuration
        eCustomDevice = 0
        ## Autoconfiguration of the audioStream for the Skinetic device
        eSkinetic = 1
        ## Autoconfiguration of the audioStream for the HSD mk.I device
        eHSDmkI = 2
        ## Autoconfiguration of the audioStream for the HSD mk.II device
        eHSDmkII = 3
        ## Autoconfiguration of the audioStream for the HSD 0 device
        eHSD0 = 4
        ## Autoconfiguration of the audioStream for the HSD mk.III device
        eHSDmkIII = 5

        @classmethod
        def from_param(cls, obj):
            """ @private
            """
            if not isinstance(obj, cls):
                raise TypeError
            return obj.value

    class OutputType(Enum):
        """ Enum to define the target output mode.
        """
        ## Connection type is automatically handled
        AutoDetect = 0
        ## Bluetooth connection
        Bluetooth = 1
        ## USB connection
        USB = 2
        ## Wifi connection
        Wifi = 3

        @classmethod
        def from_param(cls, obj):
            """ @private
            """
            if not isinstance(obj, cls):
                raise TypeError
            return obj.value

    class DeviceType(Enum):
        """ Enum to define the type of device
        """
        ## Type is Unknown or undefined
        Unknown = -1
        ## Skinetic Vest
        Vest = _DEV_TYPE_SKINETIC
        ## HSD mk.II development kit
        HSDmk2 = _DEV_TYPE_HSDMK2
        ## HSD mk.III development kit
        HSDmk3 = _DEV_TYPE_HSDMK3

        @classmethod
        def from_param(cls, obj):
            """ @private
            """
            if not isinstance(obj, cls):
                raise TypeError
            return obj.value

    class EffectState(Enum):
        """ Enum describing the state of an effect instance
        """
        ## Effect is playing
        ePlay = 2
        ## Effect is muted
        eMute = 1
        ## Effect is initialized and should play as soon as possible
        eInitialized = 0
        ## Effect is stopped
        eStop = -1

        @classmethod
        def from_param(cls, obj):
            """ @private
            """
            if not isinstance(obj, cls):
                raise TypeError
            return obj.value

    class DeviceInfo:
        """ Class returned by a scan and containing all information
        of an introspected device.
        """

        def __init__(self, out_type, serial_nb, dev_type, dev_version):
            ## Available Output connection mode
            self.output_type = Skinetic.OutputType(out_type)
            ## Device Serial Number
            self.serial_number = int(serial_nb)
            ## Device Type
            self.device_type = Skinetic.DeviceType(dev_type)
            if isinstance(dev_version, bytes):
                self.device_version = dev_version.decode('utf8')
            else:
                self.device_version = str(dev_version)

        def __str__(self):
            return ("Skinetic " + self.output_type.name + " device.\n  S/N: " +
                    str(self.serial_number) + "\n  Type: " + self.device_type.name +
                    "\n  Version: " + self.device_version)

    ## \cond
    __log_callback_set = False
    __log_cb = None
    _C_LIB = None

    _log_callback_type = CFUNCTYPE(None, c_int, c_char_p, c_char_p)
    _SFL_C_API = {
        "ski_serialNumberToString": (c_char_p, [c_uint32]),
        "ski_createSDKInstance": (_handle_error_code, [c_char_p]),
        "ski_freeSDKInstance": (None, [c_int]),
        "ski_scanDevices": (_handle_error_code, [c_int, OutputType]),
        "ski_scanStatus": (_handle_error_code, [c_int]),
        "ski_getFirstScannedDevice": (POINTER(_DeviceInfo), [c_int]),
        "ski_connectDevice": (_handle_error_code, [c_int, OutputType, c_uint32]),
        "ski_disconnectDevice": (_handle_error_code, [c_int]),
        "ski_connectionStatus": (ConnectionState, [c_int]),
        "ski_setConnectionCallback": (_handle_error_code, [c_int, CFUNCTYPE(None, c_int, c_int, c_uint32, c_void_p)]),
        "ski_getSDKVersion": (c_char_p, [c_int]),
        "ski_getSkineticSerialNumber": (c_uint32, [c_int]),
        "ski_getSkineticVersion": (c_char_p, [c_int]),
        "ski_getSkineticType": (DeviceType, [c_int]),
        "ski_getGlobalIntensityBoost": (_handle_error_code, [c_int]),
        "ski_setGlobalIntensityBoost": (_handle_error_code, [c_int, c_int]),
        "ski_loadPatternFromJSON": (_handle_error_code, [c_int, c_char_p]),
        "ski_unloadPattern": (_handle_error_code, [c_int, c_int]),
        "ski_getPatternIntensityBoost": (_handle_error_code, [c_int, c_int]),
        "ski_setAccumulationWindowToPattern": (_handle_error_code, [c_int, c_int, c_int, c_float, c_int]),
        "ski_eraseAccumulationWindowToPattern": (_handle_error_code, [c_int]),
        "ski_defaultEffectProperties": (None, [POINTER(_CEffectProperties)]),
        "ski_playEffect": (_handle_error_code, [c_int, c_int, _CEffectProperties]),
        "ski_stopEffect": (_handle_error_code, [c_int, c_int, c_float]),
        "ski_effectState": (EffectState, [c_int, c_int]),
        "ski_getSkineticSerialNumberAsString": (c_char_p, [c_int]),
        "ski_pauseAll": (_handle_error_code, [c_int]),
        "ski_resumeAll": (_handle_error_code, [c_int]),
        "ski_stopAll": (_handle_error_code, [c_int]),
        "ski_setLogCallback": (_handle_error_code, [_log_callback_type]),
        "ski_exp_enableLegacyBackend": (None, [c_int, c_bool]),
        "ski_exp_connectAsh": (_handle_error_code, [c_int, OutputType, c_uint32, c_char_p]),
        "ski_exp_connectAudio": (_handle_error_code, [c_int, ExpAudioPreset, _CAudioSettings]),
        "ski_exp_connectAshAudio": (_handle_error_code, [c_int, ExpAudioPreset, _CAudioSettings, c_char_p]),
        "ski_exp_setAshVolume": (_handle_error_code, [c_int, c_float]),
        "ski_exp_getAshVolume": (c_float, [c_int]),
        "ski_exp_setAshPreset": (_handle_error_code, [c_int, c_int]),
        "ski_exp_getAshPreset": (c_int, [c_int]),
        "ski_exp_listAshPresets": (_handle_error_code, [POINTER(POINTER(c_char_p)), POINTER(c_int)]),
        "ski_exp_getOutputDevicesNames": (_handle_error_code, [POINTER(POINTER(c_char_p)), POINTER(c_int)]),
        "ski_exp_getLoopbackDevicesNames": (_handle_error_code, [POINTER(POINTER(c_char_p)), POINTER(c_int)]),
        "ski_exp_getOutputDeviceAPIs": (_handle_error_code, [c_char_p, POINTER(POINTER(c_char_p)), POINTER(c_int)]),
        "ski_exp_getOutputDeviceInfo": (
            _handle_error_code, [c_char_p, c_char_p, POINTER(c_int), POINTER(c_float), POINTER(c_float)]),
        "ski_exp_getSupportedStandardSampleRates": (
            _handle_error_code, [c_char_p, c_char_p, POINTER(POINTER(c_uint32)), POINTER(c_int)]),
    }

    ## \endcond

    def __init__(self, log_file_path: str = ""):
        """ Instantiate a new Skinetic SDK.

        Args:
            log_file_path: (str, optional) path of the file to log in. Leave blank to use the
               default SkineticSDK.log file. File path can be absolute or
               relative to the directory containing the library.
        """
        self.__handle = None
        self.__con_cb = None
        self.__scan_in_progress = False
        self.__scan_result_read = True
        self.__device_list: typing.List[Skinetic.DeviceInfo] = []
        Skinetic._init_c_lib()
        if Skinetic.__log_callback_set:
            Skinetic._static_call("ski_setLogCallback", Skinetic.__log_cb)
        self.__handle = Skinetic._static_call(
            "ski_createSDKInstance", log_file_path.encode('utf8'))

    ## \cond
    def __del__(self):
        if Skinetic._C_LIB is not None and self.__handle is not None:
            self._call("ski_freeSDKInstance")
    ## \endcond

    @staticmethod
    def serial_number_to_str(serial_number: int = 0) -> str:
        """ Convert Skinetic serial number to a formatted string.

        Args:
            serial_number: (int) serial number to convert.

        Returns:
            (str) string representation of the serial number.
        """
        return Skinetic._static_call("ski_serialNumberToString", serial_number).decode('utf8')

    def update_device_list(self, output_type: OutputType = OutputType.AutoDetect,
                           blocking: bool = True):
        """ Initialize a scanning routine to find all available Skinetic device.

        Args:
            output_type: (OutputType, optional) type of connection to introspect.
            blocking: (bool, optional) defines whether the method is blocking during the scan or not.
        """
        self.__start_scan(output_type)
        if blocking:
            self.__wait_end_of_scan()

    def get_device_list(self, wait_end_of_scan: bool = False) -> list:
        """ Get the list of devices.
        This method retrieves the list of devices that have been scanned. You can optionally wait for
        the scan to finish before fetching the device list.

        Args:
            wait_end_of_scan: (bool, optional) A boolean flag to indicate whether to wait for the scan to be finished.
                                Default is False.

        Returns:
            list of DeviceInfo
        """
        if wait_end_of_scan:
            self.__wait_end_of_scan()
        if self.__scan_in_progress:
            raise Skinetic.DeviceScanInProgress(
                "Cannot read device list: scan is still in progress")
        self.__process_scan_result()
        return self.__device_list

    def connect(self, output_type: OutputType = OutputType.AutoDetect,
                serial_number: int = 0, blocking: bool = True):
        """ Initialize an asynchronous or synchronous connection to a Skinetic
        device using the selected type of connection.

        The state of the routine can be obtained from get_connection_state().
        If the serial number is set to 0, the connection will be performed on the first found device.

        Args:
            output_type: (OutputType, optional) type of connection device. Defaults to OutputType.AutoDetect.
            serial_number: (int, optional) serial number of the Skinetic Device to connect to. Defaults to 0.
            blocking: (bool, optional) A boolean flag to indicate whether to wait for the connection to finish.
                                       Default is True.
        """
        self._call("ski_connectDevice", output_type, serial_number)
        if blocking:
            while self.get_connection_state() == Skinetic.ConnectionState.Connecting:
                time.sleep(0.001)
            if self.get_connection_state() != Skinetic.ConnectionState.Connected:
                raise ConnectionError("Failed to connect to device")

    def disconnect(self, blocking: bool = True):
        """ Disconnects the current Skinetic device
        The disconnection is effective once all resources are released.
        The state of the routine can be obtained from get_connection_state().

        Args:
            blocking: (bool, optional) A boolean flag to indicate whether to wait for the disconnection to finish.
                            Defaults to True.
        """
        self._call("ski_disconnectDevice")
        if blocking:
            while self.get_connection_state() == Skinetic.ConnectionState.Disconnecting:
                time.sleep(0.001)
            if self.get_connection_state() != Skinetic.ConnectionState.Disconnected:
                raise ConnectionError("Failed to disconnect device")

    def get_connection_state(self) -> ConnectionState:
        """ Check the current status of the connection.
        The asynchronous connection routine is terminated on failure

        Returns:
            (ConnectionState) status of connection of the device
        """
        return self._call("ski_connectionStatus")

    def set_connection_callback(self, callback: typing.Callable[[ConnectionState, SkineticErrorCode, int], None]):
        """ Set a callback function fired upon connection changes.
        The connection callback is implemented by users (you).

        The callback is fired at the end of the connection routine whether it succeeded
        or failed. It is also fired if a connection issue arise.
        Pass None to set_connection_callback() to disable the callback.

        The callback function will be called with the following parameters:
        - connection_state (ConnectionState): status of the connection
        - error_code (SkineticErrorCode): type of error occurring
        - serial_number (int): serial number of the device firing the callback

        Args:
            callback: (typing.Callable[[ConnectionState, SkineticErrorCode, int], None]) Client's callback function
        """

        @CFUNCTYPE(None, c_int, c_int, c_uint32, c_void_p)
        def cb(new_status: int, error: int, serial_number: int, _):
            callback(Skinetic.ConnectionState(new_status),
                     SkineticErrorCode(error), serial_number)

        self.__con_cb = cb
        self._call("ski_setConnectionCallback", cb, None)

    @staticmethod
    def set_log_callback(callback: typing.Callable[[LogLevel, str, str], None] = None):
        """ Set a callback function to override Skinetic native logger.
        It should be set before initialisation of a Skinetic object.
        This callback will be called every time a log message is produced by the SDK.

        The callback function takes the following arguments:
            - level (LogLevel): the level of the log message
            - scope (str): string giving information about the SDK scope that send the log
            - message (str): string containing the log message

        Set the callback to None will disable logging.
        Args:
            callback: (typing.Callable[[LogLevel, str, str], None]) Client's callback function
        """

        Skinetic.__log_callback_set = True
        if callback is not None:
            @CFUNCTYPE(None, c_int, c_char_p, c_char_p)
            def cb(level: int, scope: str, message: str):
                callback(Skinetic.LogLevel(level), scope, message)
            Skinetic.__log_cb = cb
        else:
            Skinetic.__log_cb = cast(None, Skinetic._log_callback_type)

    def get_sdk_version(self) -> str:
        """ Get SDK version as a string.
        The format of the string is: <pre>major.minor.revision</pre>

        Returns:
            (str) version string
        """
        return self._call("ski_getSDKVersion").decode('utf8')

    def get_skinetic_serial_number(self) -> int:
        """ Get the connected device's serial number.

        Returns:
            (int) the serial number of the connected Skinetic device if any,
            raises an Exception otherwise.
        """
        return self._call("ski_getSkineticSerialNumber")

    def get_skinetic_serial_number_as_str(self) -> str:
        """ Get the connected device's serial number as a string.

        Returns:
            (str) the serial number as a string of the connected Skinetic device if any, "noDeviceConnected"
        otherwise.
        """
        return self._call("ski_getSkineticSerialNumberAsString").decode('utf8')

    def get_skinetic_version(self) -> str:
        """ Get the connected device's version as a string.
        The format of the string is: <pre>major.minor.revision</pre>

        Returns:
            (str) the version string if a Skinetic device is connected, "noDeviceConnected" otherwise.
        """
        return self._call("ski_getSkineticVersion").decode('utf8')

    def get_skinetic_type(self) -> DeviceType:
        """ Get the connected device's type.

        Returns:
            (DeviceType) type of the connected Skinetic device if it is connected,
            raises an Exception otherwise.
        """
        return self._call("ski_getSkineticType")

    def get_global_intensity_boost(self) -> int:
        """ Get the amount of global intensity boost.
        This boost increase the overall intensity of all haptic effects.
        However, the higher the boost activation is, the more the haptic effects are degraded.
        The global boost is meant to be set by the user as an application setting.

        Returns:
            (int) the global intensity boost.
        """
        return self._call("ski_getGlobalIntensityBoost")

    def set_global_intensity_boost(self, global_boost: int):
        """ Set the amount of global intensity boost.
        The boost increase the overall intensity of all haptic effects.
        However, the higher the boost activation is, the more the haptic effects are degraded.
        The global boost is meant to be set by the user as an application setting.

        Returns:
            global_boost (int) global boost value
        """
        self._call("ski_setGlobalIntensityBoost", c_int(global_boost))

    def load_pattern_json(self, json_string: str) -> int:
        """ Load a pattern from a valid json into a local haptic asset and return
        the corresponding pattern_id.

        Args:
            json_string: (str) json describing the pattern.

        Returns:
            (int) positive pattern_id on success, raises an Exception otherwise.
        """
        return self._call("ski_loadPatternFromJSON",
                          json_string.encode('utf8'))

    def unload_pattern(self, pattern_id: int):
        """ Unload the pattern from of the corresponding pattern_id.

        Args:
            pattern_id: (int) id of the pattern to unload.
        """
        self._call("ski_unloadPattern", pattern_id)

    def get_pattern_intensity_boost(self, pattern_id: int) -> int:
        """ Get the pattern boost value which serves as a default value for the playing effect.
        The value is ranged in [-100; 100].

        Args:
            pattern_id: (int) the ID of the targeted pattern.

        Returns:
            (int) the pattern intensity boost of the pattern if it exists, 0 otherwise.
        """
        return self._call("ski_getPatternIntensityBoost", pattern_id)

    def set_accumulation_window_to_pattern(self, main_pattern_id: int, fallback_pattern_id: int,
                                           time_window: float, max_accumulation: int):
        """ Enable the effect accumulation strategy on a targeted pattern.
        Whenever an effect is triggered on the main pattern,
        the fallback one is used instead, if the main is already playing.
        More details can be found the additional documentation.
        For the max_accumulation, setting to 0 removes the limit.

        If a new call to this function is done for a specific pattern, the previous association is overridden.

        Args:
            main_pattern_id: (int) the pattern_id of the main pattern
            fallback_pattern_id: (int) the pattern_id of the fallback pattern
            time_window: (float) the time window during which the accumulation should happen
            max_accumulation: (int) max number in [0; infinity[ of extra accumulated effect instances
        """
        self._call("ski_setAccumulationWindowToPattern", main_pattern_id,
                   fallback_pattern_id, time_window, max_accumulation)

    def erase_accumulation_pattern(self, main_pattern_id: int):
        """ Disable the effect accumulation strategy on a specific pattern if any set.

        Args:
            main_pattern_id: (int) the pattern_id of the main pattern.
        """
        self._call("ski_eraseAccumulationWindowToPattern", main_pattern_id)

    def play_effect(self, pattern_id: int, properties: EffectProperties = EffectProperties()) -> int:
        """ Play a haptic effect based on a loaded pattern and return the effectID of this instance.
        The instance index is positive. Each call to play_effect() using the same pattern_id
        generates a new haptic effect instance totally uncorrelated to the previous ones.
        The instance is destroyed once it stops playing.

        The haptic effect instance reproduces the pattern with variations describes
        in the class EffectProperties(). More information on these parameters and how to use them can be found
        in the class description. Transformation and boolean operations are not applicable to
        actuator-based patterns.
        If the pattern is unloaded, the haptic effect is not interrupted.

        Args:
            pattern_id: (int) id of the pattern used by the effect instance.
            properties: (EffectProperties) specialisation properties of the effect.

        Returns:
            (int) id of the effect instance
        """
        cprops = properties.to_c_type()
        return self._call("ski_playEffect", pattern_id, cprops)

    def stop_effect(self, effect_id: int, fadeout: float = 0):
        """ Stop the effect instance identified by its effectID.
        The effect is stop in "time" seconds with a fadeout to prevent abrupt transition.
        If "time" is set to 0, no fadeout is applied and the effect is stopped as soon as possible.
        Once an effect is stopped, its instance is destroyed and its effect_id invalidated.

        Args:
            effect_id: (int) index identifying the effect.
            fadeout: (float, optional) time duration of the fadeout in second.
        """
        self._call("ski_stopEffect", effect_id, fadeout)

    def get_effect_state(self, effect_id: int) -> EffectState:
        """ Get the current state of an effect.
        If the effect_id is invalid, the 'stop' state will be returned.

        Args:
            effect_id: (int) index identifying the effect.

        Returns:
            (EffectState) the current state of the effect.
        """
        return self._call("ski_effectState", effect_id)

    def pause_all(self):
        """ Pause all haptic effect that are currently playing.
        """
        self._call("ski_pauseAll")

    def resume_all(self):
        """ Resume the paused haptic effects.
        """
        self._call("ski_resumeAll")

    def stop_all(self):
        """ Stop all playing haptic effect.
        """
        self._call("ski_stopAll")

    def exp_enable_legacy_backend(self, enable: bool):
        """ [Experimental] Enable legacy backend
        If boolean is set to true, the legacy backend is used instead of the default backend.

        Args:
            enable: set to true to enable, false to disable.
        """
        self._call("ski_exp_enableLegacyBackend", enable)

    def exp_connect_ash(self, output_type: OutputType = OutputType.AutoDetect, serial_number: int = 0,
                        loopback_interface: str = "", blocking: bool = True):
        """ [Experimental] Initialize an asynchronous connection to a Skinetic device and use
        the ASH-fx library for haptic generation.

        The ASH-fx library generates haptic signals based on the audio of the targeted 
        input loopback interface. 
        The loopback interfaces can be queried by calling getLoopbackDevicesNames(). 
        Setting loopbackInterface to NULL selects the default audio output device 
        of the system.

        Args:
            output_type: (OutputType, optional) type of connection device. Defaults to OutputType.AutoDetect.
            serial_number: (int, optional) serial number of the Skinetic Device to connect to. Defaults to 0.
            loopback_interface: (str, optional) input loopback interface to use (to feed ASH). Defaults to the default
                                                output device of the computer.
            blocking: (bool, optional) A boolean flag to indicate whether to wait for the connection to finish.
                                       Default is True.
        """
        if len(loopback_interface) > 0:
            loopback = loopback_interface.encode('utf8')
        else:
            loopback = None
        self._call("ski_exp_connectAsh", output_type, serial_number, loopback)
        if blocking:
            while self.get_connection_state() == Skinetic.ConnectionState.Connecting:
                time.sleep(0.001)
            if self.get_connection_state() != Skinetic.ConnectionState.Connected:
                raise ConnectionError("Failed to connect to device")

    def exp_connect_audio(self, preset: ExpAudioPreset = ExpAudioPreset.eCustomDevice,
                          device_name: str = "default_output", audio_api: str = "any_API", sample_rate: int = 48000,
                          buffer_size: int = 256, nb_stream_channels: int = -1,
                          suggested_latency: float = 0
                          ):
        """ [Experimental] Initialize an asynchronous connection to an audio device using the provided settings.
        
        If ExpAudioPreset is set to anything else other than ExpAudioPreset.eCustomDevice,
        the provided settings are ignored and the ones corresponding to the preset are used instead.
        Notice that this connection is not compatible with the legacy backend.

        Args:
            preset: (ExpAudioPreset, optional) preset of audio device.
            device_name: name of the targeted audio device, default value "default_output" uses the
                        OS default audio device. If using a specific ExpAudioPreset other than
                        eCustomDevice, the parameter will be ignored.
            audio_api: name of the targeted API. Default value "any_API" uses any available
                        API which match the configuration, if any. If using a specific
                        ExpAudioPreset other than eCustomDevice, the parameter will be ignored.
            sample_rate: sample rate of the audio stream.
                        If using a specific ExpAudioPreset,the parameter will be ignored.
            buffer_size: size (strictly positive) of a chunk of data sent over the audio stream.
                        This parameter MUST be set independently of the used ExpAudioPreset.
            nb_stream_channels: number of channels (strictly positive) to use while streaming to the haptic output.
                        If using a specific audio preset, the parameter will be ignored.
                        Setting -1 will use the number of actuator of the layout, or a portion of it.
            suggested_latency: desired latency in seconds. The value is rounded to the closest available latency value
                        from the audio API. If using a specific ExpAudioPreset
                        other than eCustomDevice, the parameter will be ignored.
        """
        settings = _CAudioSettings()
        settings.device_name = c_char_p(device_name.encode('utf8'))
        settings.audio_api = c_char_p(audio_api.encode('utf8'))
        settings.sample_rate = c_uint(sample_rate)
        settings.buffer_size = c_uint(buffer_size)
        settings.nb_stream_channel = c_int(nb_stream_channels)
        settings.suggested_latency = c_float(suggested_latency)
        self._call("ski_exp_connectAudio", preset, settings)

    def exp_connect_ash_audio(self, preset: ExpAudioPreset = ExpAudioPreset.eCustomDevice,
                              device_name: str = "default_output", audio_api: str = "any_API", sample_rate: int = 48000,
                              buffer_size: int = 256, nb_stream_channels: int = -1, suggested_latency: float = 0,
                              loopback_interface: str = ""):
        """ [Experimental] Initialize an asynchronous connection to an audio device and use
        the ASH-fx library for haptic generation.

        The ASH-fx library generates haptic signals based on the audio of the targeted 
        input loopback interface. 
        The loopback interfaces can be queried by calling getLoopbackDevicesNames(). 
        Setting loopbackInterface to NULL selects the default audio output device 
        of the system.
        If ExpAudioPreset is set to anything else other than ExpAudioPreset.eCustomDevice,
        the provided settings are ignored and the ones corresponding to the preset are used instead.

        Args:
            preset: (ExpAudioPreset, optional) preset of audio device.
            device_name: name of the targeted audio device, default value "default_output" uses the
                        OS default audio device. If using a specific ExpAudioPreset other than
                        eCustomDevice, the parameter will be ignored.
            audio_api: name of the targeted API. Default value "any_API" uses any available
                        API which match the configuration, if any. If using a specific
                        ExpAudioPreset other than eCustomDevice, the parameter will be ignored.
            sample_rate: sample rate of the audio stream.
                        If using a specific ExpAudioPreset,the parameter will be ignored.
            buffer_size: size (strictly positive) of a chunk of data sent over the audio stream.
                        This parameter MUST be set independently of the used ExpAudioPreset.
            nb_stream_channels: number of channels (strictly positive) to use while streaming to the haptic output.
                        If using a specific audio preset, the parameter will be ignored.
                        Setting -1 will use the number of actuator of the layout, or a portion of it.
            suggested_latency: desired latency in seconds. The value is rounded to the closest available latency value
                        from the audio API. If using a specific ExpAudioPreset
                        other than eCustomDevice, the parameter will be ignored.
            loopback_interface: (str, optional) input loopback interface to use (to feed ASH). Defaults to the default
                                                output device of the computer.
        """
        settings = _CAudioSettings()
        settings.device_name = c_char_p(device_name.encode('utf8'))
        settings.audio_api = c_char_p(audio_api.encode('utf8'))
        settings.sample_rate = c_uint(sample_rate)
        settings.buffer_size = c_uint(buffer_size)
        settings.nb_stream_channel = c_int(nb_stream_channels)
        settings.suggested_latency = c_float(suggested_latency)
        if len(loopback_interface) > 0:
            loopback = loopback_interface.encode('utf8')
        else:
            loopback = None
        self._call("ski_exp_connectAshAudio", preset, settings, loopback)

    def exp_set_ash_volume(self, volume: float):
        """ [Experimental] Set the volume of the ASH-generated haptic track.

        Args:
            volume: (float) normalized haptic volume (1 means 100%, values above 1 can be used but might produce clipping).
        """
        self._call("ski_exp_setAshVolume", volume)

    def exp_get_ash_volume(self) -> float:
        """ [Experimental] Get the volume of the ASH-generated haptic track.

        Returns:
            (float) the normalized volume of the ASH-generated haptic track.
        """
        volume = self._call("ski_exp_getAshVolume")
        if volume < 0:
            _handle_error_code(int(volume))
        return volume

    def exp_set_ash_preset(self, preset_index: int):
        """ [Experimental] Set the mode of generation for the ASH-fx library.

        Args:
            preset_index: (int) index of the preset to use. The list of available presets can be listed with `exp_list_ash_presets`.
        """
        self._call("ski_exp_setAshPreset", preset_index)

    def exp_get_ash_preset(self) -> int:
        """ [Experimental] Get the current mode of generation for the ASH-fx library.

        Returns:
            (int) the current preset index.
        """
        return self._call("ski_exp_getAshPreset")

    @staticmethod
    def exp_list_ash_presets() -> list:
        """ [Experimental] Get names of available ASH-fx generation modes (presets).

        Returns:
            (str list) the list of preset names.
        """
        preset_name_list = []
        preset_names = POINTER(c_char_p)()
        nb_presets = c_int()
        Skinetic._static_call("ski_exp_listAshPresets", byref(preset_names),
                              byref(nb_presets))
        for i in range(nb_presets.value):
            preset_name_list.append(preset_names[i].decode())
        return preset_name_list

    @staticmethod
    def exp_get_output_device_names() -> list:
        """ [Experimental] Get names of available output devices.
        If no device is available, the list will contain "noDevice".

        Returns:
            (str list) the list of device names.
        """
        devices_names_list = []
        devices_names = POINTER(c_char_p)()
        nb_devices = c_int()
        Skinetic._static_call("ski_exp_getOutputDevicesNames",
                              byref(devices_names), byref(nb_devices))
        for i in range(0, nb_devices.value):
            devices_names_list.append(devices_names[i].decode())
        return devices_names_list

    @staticmethod
    def exp_get_loopback_device_names() -> list:
        """ [Experimental] Get names of available loopback devices.
        If no device is available, the list will contain "noDevice".

        Returns:
            (str list) the list of device names.
        """
        devices_names_list = []
        devices_names = POINTER(c_char_p)()
        nb_devices = c_int()
        Skinetic._static_call("ski_exp_getLoopbackDevicesNames",
                              byref(devices_names), byref(nb_devices))
        for i in range(0, nb_devices.value):
            devices_names_list.append(devices_names[i].decode())
        return devices_names_list

    @staticmethod
    def exp_get_output_devices_apis(output_name: str) -> list:
        """ [Experimental] Get available APIs for a given output device identified by name.
        If no API is available, the list will contain "noAPI".
        This function will fill the list passed as argument with the names of
        the APIs available to the given output device

        Args:
            output_name: (str) name of the output device.

        Returns:
            (str list) list of API names
        """
        api = []
        c_output_name = c_char_p()
        apis = POINTER(c_char_p)()
        nb_apis = c_int()
        c_output_name.value = output_name.encode('utf8')
        Skinetic._static_call("ski_exp_getOutputDeviceAPIs", c_output_name,
                              byref(apis), byref(nb_apis))
        for i in range(0, nb_apis.value):
            api.append(apis[i].decode())
        return api

    @staticmethod
    def exp_get_output_device_info(name: str, api: str) -> dict:
        """ [Experimental] Get settings extremum values of the output device identified by name and API.

        Args:
            name: (str) name of the output device.
            api: (str) name of the API.

        Returns:
            (dict) a dictionary containing the values for the specified device and api
        """
        info = {}
        output_name = c_char_p(name.encode('utf8'))
        api_name = c_char_p(api.encode('utf8'))
        max_channels = c_int()
        def_low_latency = c_float()
        def_high_latency = c_float()
        Skinetic._static_call('ski_exp_getOutputDeviceInfo', output_name,
                              api_name, byref(max_channels), byref(def_low_latency),
                              byref(def_high_latency))
        info['output_device'] = output_name.value.decode()
        info['api'] = api_name.value.decode()
        info['max_channels'] = max_channels.value
        info['default_low_latency'] = def_low_latency.value
        info['default_high_latency'] = def_high_latency.value
        return info

    @staticmethod
    def exp_get_supported_standard_sample_rates(output_name: str,
                                                output_api: str) -> list:
        """ [Experimental] Get all supported standard sample rates for the output device identified by name and API.
        If the output_name or the output_api are not valid, an error is raised.

        Args:
            output_name: (str) name of the output device.
            output_api: (str) name of the API.

        Returns:
            (list) list of sample rates
        """
        sample_rates = []
        name = c_char_p()
        api = c_char_p()
        name.value = output_name.encode('utf8')
        api.value = output_api.encode('utf8')
        sample_rates_arr = POINTER(c_uint32)()
        nb_sample_rates = c_int()
        Skinetic._static_call('ski_exp_getSupportedStandardSampleRates', name, api, byref(sample_rates_arr),
                              byref(nb_sample_rates))
        for i in range(0, nb_sample_rates.value):
            sample_rates.append(sample_rates_arr[i])
        return sample_rates

    def __start_scan(self, output_type: OutputType):
        self._call("ski_scanDevices", output_type)
        self.__scan_in_progress = True
        self.__scan_result_read = False

    def __update_scan_status(self):
        self.__scan_in_progress = (self._call("ski_scanStatus") != 0)

    def __wait_end_of_scan(self):
        while self.__scan_in_progress:
            time.sleep(0.001)
            self.__update_scan_status()
        self.__process_scan_result()

    def __process_scan_result(self):
        """ Gets a list of all the Skinetic devices found during the scan
        which match the specified output type.
        """
        if self.__scan_result_read:
            return
        self.__device_list = []
        self.__scan_result_read = True
        p_dev: POINTER(_DeviceInfo) = self._call("ski_getFirstScannedDevice")
        while p_dev:
            dev = p_dev.contents
            dev_info = Skinetic.DeviceInfo(dev.outputType, dev.serialNumber,
                                           dev.deviceType, dev.deviceVersion)
            self.__device_list.append(dev_info)
            p_dev = dev.next

    ## \cond
    @staticmethod
    def _init_c_lib():
        if Skinetic._C_LIB is not None:
            return
        root = abspath(dirname(__file__))
        s = platform.system()
        try:
            if s == "Windows":
                Skinetic._C_LIB = cdll.LoadLibrary(join(root, "SkineticSDK.dll"))
            elif s == "Linux":
                Skinetic._C_LIB = cdll.LoadLibrary(join(root, "libSkineticSDK.so"))
            elif s == "Darwin":
                Skinetic._C_LIB = cdll.LoadLibrary(join(root, "libSkineticSDK.dylib"))
            else:
                raise RuntimeError("Unsupported platform: " + s)
            Skinetic._init_c_lib_prototypes()
        except (Exception, KeyboardInterrupt):
            Skinetic._C_LIB = None
            raise

    @staticmethod
    def _init_c_lib_prototypes():
        for f_name, signature in Skinetic._SFL_C_API.items():
            c_func = getattr(Skinetic._C_LIB, f_name)
            c_func.restype = signature[0]
            c_func.argtypes = signature[1]

    @staticmethod
    def _static_call(f_name, *args):
        Skinetic._init_c_lib()
        c_func = getattr(Skinetic._C_LIB, f_name)
        return c_func(*args)

    def _call(self, f_name, *args):
        c_func = getattr(Skinetic._C_LIB, f_name)
        return c_func(self.__handle, *args)
    ## \endcond


## \cond
_DeviceInfo._fields_ = [
    ("outputType", c_int),  # Available Output connection mode
    ("serialNumber", c_uint32),  # Device Serial Number
    ("deviceType", c_int),  # Device Type
    ("deviceVersion", c_char_p),  # Device Version
    ("next", POINTER(_DeviceInfo)),  # Pointer to the next device
]
## \endcond
