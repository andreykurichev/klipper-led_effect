from math import cos, exp, pi


COLORS = 4

GAMMA_TABLE = []
GAMMA_SIZE = 4096
GAMMA = 2.4

LINEAR_CUTOFF = 0.003
LINEAR_SLOPE = 12.92

for i in range(GAMMA_SIZE):
    x = i / (GAMMA_SIZE - 1.0)
    if x <= LINEAR_CUTOFF:
        y = x * LINEAR_SLOPE
    else:
        y = (1 + 0.055) * pow(x, 1.0 / GAMMA) - 0.055
    GAMMA_TABLE.append(y)

######################################################################
# Custom color value list, returns lists of [r, g ,b] values
# from a one dimensional list
######################################################################

class colorArray(list):
    def __init__(self, num_colors, kwargs):
        self.n=num_colors
        super(colorArray,self).__init__(kwargs)
        
    def __getitem__(self, a):
        if isinstance(a, int):
            return super(colorArray, self).__getitem__(
                            slice(a*self.n, a*self.n+self.n))
        if isinstance(a, slice):
                start = a.start*self.n if a.start != None else None
                stop = a.stop*self.n if a.stop != None else None
                return colorArray(self.n,
                        super(colorArray, self).__getitem__(
                            slice(start, stop, a.step)))
    def __getslice__(self, a, b):
        return self.__getitem__(slice(a,b))
    def __setitem__(self, a, v):
        if isinstance(a, int):
            for i in range(self.n):
                super(colorArray, self).__setitem__(a*self.n + i, v[i])
    def __len__(self):
        return super(colorArray, self).__len__() // self.n
    def reverse(self):
        self.__init__(self.n, [c for cl in range(len(self)-1,-1, -1)
                        for c in self[cl]])
    def shift(self, shift=1, direction=True):
        if direction:
            shift *= -1
        self.__init__(self.n, self[shift:] + self[:shift])
    def padLeft(self, v, a):
        self.__init__(self.n, v * a + self)
    def padRight(self, v, a):
        self += v * a

######################################################################
# LED Effect handler
######################################################################

class ledFrameHandler:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode   = self.printer.lookup_object('gcode')
        self.printer.load_object(config, "display_status")
        self.printProgress = 0
        self.effects = []
        self.state = "idle"
        self.door_open = False
        self.print_completed = False
        self.completed_exited = False
        self.door_button_pin = config.get('door_button_pin', None)
        self.door_button_open_state = config.get('door_button_open_state', 'pressed').strip().lower()
        self.door_button_raw_state = None
        self.pause_resume = None
        self.virtual_sdcard = None
        self.gcode_move = None
        self.print_session_active = False
        self.first_layer_print_detected = False
        self._print_detect_last_e = None
        self._print_detect_extruded = 0.0
        self.filament_status_objects = []
        self.filament_attention_latched = False
        self.filament_attention_last_reason = ""
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.ledChains=[]
        self.gcode.register_command('STOP_LED_EFFECTS',
                                    self.cmd_STOP_LED_EFFECTS,
                                    desc=self.cmd_STOP_LED_EFFECTS_help)

        self.gcode.register_command('LED_EFFECT_DEBUG_STATUS',
                                  self.cmd_LED_EFFECT_DEBUG_STATUS,
                                  desc="Show led_effect state and Klipper printer objects used by status effects")        

        self.on_off_pin_name = config.get('on_off_pin', None)
        self.on_off_pin = None        
        self.shutdown = False

        if self.door_button_pin:
            buttons = self.printer.load_object(config, "buttons")
            buttons.register_buttons([self.door_button_pin], self._door_button_callback)

    cmd_STOP_LED_EFFECTS_help = 'Stops all led_effects'

    def _door_button_callback(self, eventtime, state):
        # Internal physical door input.
        # Klipper buttons passes state as 1/True for pressed, 0/False for released.
        self.door_button_raw_state = bool(state)

        if self.door_button_open_state in ("pressed", "press", "1", "true", "open"):
            self.door_open = bool(state)
        elif self.door_button_open_state in ("released", "release", "0", "false", "closed"):
            self.door_open = not bool(state)
        else:
            # Safe default for current Qidi wiring: pressed means door open.
            self.door_open = bool(state)

        self._update_state()


    def _debug_respond(self, gcmd, msg):
        try:
            gcmd.respond_info(str(msg))
        except Exception:
            try:
                self.gcode.respond_info(str(msg))
            except Exception:
                pass

    def _debug_safe_status(self, object_name, eventtime):
        try:
            obj = self.printer.lookup_object(object_name, None)
            if obj is None:
                return None
            if hasattr(obj, "get_status"):
                return obj.get_status(eventtime)
            return {"object": "present", "get_status": "not_available"}
        except Exception as e:
            return {"error": str(e)}

    def _debug_list_objects(self):
        names = []
        try:
            if hasattr(self.printer, "lookup_objects"):
                for item in self.printer.lookup_objects():
                    try:
                        names.append(item[0])
                    except Exception:
                        pass
        except Exception:
            pass
        return sorted(set(names))

    def _debug_find_objects(self, prefixes):
        names = self._debug_list_objects()
        found = []
        for name in names:
            for prefix in prefixes:
                if name == prefix or name.startswith(prefix + " "):
                    found.append(name)
                    break
        return found

    def cmd_LED_EFFECT_DEBUG_STATUS(self, gcmd):
        try:
            eventtime = self.reactor.monotonic()
        except Exception:
            try:
                eventtime = self.printer.get_reactor().monotonic()
            except Exception:
                eventtime = 0.0

        self._debug_respond(gcmd, "=== LED_EFFECT_DEBUG_STATUS ===")
        self._debug_respond(gcmd, "led_effect.state: %s" % getattr(self, "state", "unknown"))
        self._debug_respond(gcmd, "shutdown: %s" % getattr(self, "shutdown", "unknown"))
        self._debug_respond(gcmd, "door_open: %s" % getattr(self, "door_open", "unknown"))
        self._debug_respond(gcmd, "door_button_pin: %s" % getattr(self, "door_button_pin", None))
        self._debug_respond(gcmd, "door_button_open_state: %s" % getattr(self, "door_button_open_state", None))
        self._debug_respond(gcmd, "door_button_raw_state: %s" % getattr(self, "door_button_raw_state", None))
        self._debug_respond(gcmd, "print_completed: %s" % getattr(self, "print_completed", "unknown"))
        self._debug_respond(gcmd, "print_session_active: %s" % getattr(self, "print_session_active", "unknown"))
        self._debug_respond(gcmd, "filament_attention_latched: %s" % getattr(self, "filament_attention_latched", "unknown"))
        self._debug_respond(gcmd, "filament_attention_last_reason: %s" % getattr(self, "filament_attention_last_reason", "unknown"))
        self._debug_respond(gcmd, "completed_exited: %s" % getattr(self, "completed_exited", "unknown"))
        self._debug_respond(gcmd, "printProgress: %s" % getattr(self, "printProgress", "unknown"))
        self._debug_respond(gcmd, "auto_display_progress: %.5f" % self._get_display_progress_fraction(eventtime))
        self._debug_respond(gcmd, "auto_virtual_progress: %.5f" % self._get_virtual_progress_fraction(eventtime))
        self._debug_respond(gcmd, "auto_virtual_active: %s" % self._is_virtual_sdcard_active(eventtime))
        self._debug_respond(gcmd, "gcode_position: %s" % self._get_gcode_position(eventtime))
        self._debug_respond(gcmd, "first_layer_print_detected: %s" % getattr(self, "first_layer_print_detected", "unknown"))
        self._debug_respond(gcmd, "first_layer_extruded: %.5f" % getattr(self, "_print_detect_extruded", 0.0))

        fixed_objects = [
            "print_stats",
            "display_status",
            "pause_resume",
            "webhooks",
            "virtual_sdcard",
        ]

        for name in fixed_objects:
            status = self._debug_safe_status(name, eventtime)
            self._debug_respond(gcmd, "%s: %s" % (name, status))

        dynamic_prefixes = [
            "filament_switch_sensor",
            "filament_motion_sensor",
            "hall_filament_width_sensor",
            "gcode_button",
            "output_pin",
            "neopixel",
        ]

        found = self._debug_find_objects(dynamic_prefixes)
        self._debug_respond(gcmd, "found status objects: %s" % (", ".join(found) if found else "none"))

        for name in found:
            status = self._debug_safe_status(name, eventtime)
            self._debug_respond(gcmd, "%s: %s" % (name, status))

        enabled = []
        disabled = []
        for effect in self.effects:
            row = "%s idle=%s pause=%s pause_attention=%s preparing=%s progress=%s completed=%s error=%s" % (
                getattr(effect, "name", "unknown"),
                getattr(effect, "run_on_idle", False),
                getattr(effect, "run_on_pause", False),
                getattr(effect, "run_on_pause_attention", False),
                getattr(effect, "run_on_preparing", False),
                getattr(effect, "run_on_progress", False),
                getattr(effect, "run_on_completed", False),
                getattr(effect, "run_on_error", False),
            )
            if getattr(effect, "enabled", False):
                enabled.append(row)
            else:
                disabled.append(row)

        self._debug_respond(gcmd, "enabled effects:")
        for row in enabled:
            self._debug_respond(gcmd, "  " + row)

        self._debug_respond(gcmd, "disabled effects:")
        for row in disabled:
            self._debug_respond(gcmd, "  " + row)

        self._debug_respond(gcmd, "=== END LED_EFFECT_DEBUG_STATUS ===")


    def _handle_ready(self):
        self.shutdown = False
        self.reactor = self.printer.get_reactor()
        self.printer.register_event_handler('klippy:shutdown', 
                                            self._handle_shutdown)
        self.printProgress = 0
        self.displayStatus = self.printer.lookup_object('display_status')
        self.progressTimer = self.reactor.register_timer(self._pollProgress, 
                                                         self.reactor.NOW)
        self.frameTimer    = self.reactor.register_timer(self._getFrames, 
                                                         self.reactor.NOW)
                                            
        if self.on_off_pin_name:
            try:
                self.on_off_pin = self.printer.lookup_object(
                    'output_pin ' + self.on_off_pin_name)
            except:
                self.on_off_pin = None

        self.print_stats = self.printer.lookup_object('print_stats', None)
        self.pause_resume = self.printer.lookup_object('pause_resume', None)
        self.virtual_sdcard = self.printer.lookup_object('virtual_sdcard', None)
        self.gcode_move = self.printer.lookup_object('gcode_move', None)
        self.filament_status_objects = self._find_status_objects([
            "filament_switch_sensor",
            "filament_motion_sensor",
            "hall_filament_width_sensor",
        ])
        self._update_state()


    def _list_status_object_names(self):
        names = []
        try:
            if hasattr(self.printer, "lookup_objects"):
                for item in self.printer.lookup_objects():
                    try:
                        names.append(item[0])
                    except Exception:
                        pass
        except Exception:
            pass
        return sorted(set(names))

    def _find_status_objects(self, prefixes):
        found = []
        for name in self._list_status_object_names():
            for prefix in prefixes:
                if name == prefix or name.startswith(prefix + " "):
                    found.append(name)
                    break
        return found

    def _safe_object_status(self, object_name, eventtime):
        try:
            obj = self.printer.lookup_object(object_name, None)
            if obj is None or not hasattr(obj, "get_status"):
                return None
            return obj.get_status(eventtime)
        except Exception:
            return None


    def _is_pause_active(self, eventtime, print_state):
        # Prefer native pause_resume.is_paused, keep print_stats.state fallback.
        if self.pause_resume is not None:
            try:
                status = self.pause_resume.get_status(eventtime)
                if bool(status.get("is_paused", False)):
                    return True
            except Exception:
                pass
        return print_state == "paused"

    def _is_filament_attention_active(self, eventtime, latch=False):
        # Sensor attention means: a known enabled filament sensor reports
        # filament_detected == False. This is intentionally generic, so it
        # works for switch/tangle/hall sensors without extra config macros.
        for object_name in getattr(self, "filament_status_objects", []):
            status = self._safe_object_status(object_name, eventtime)
            if not status:
                continue

            if status.get("enabled", True) is False:
                continue

            if "filament_detected" in status and status.get("filament_detected") is False:
                if latch:
                    self.filament_attention_latched = True
                    self.filament_attention_last_reason = object_name
                return True

        return False


    def _get_display_progress_fraction(self, eventtime):
        try:
            if self.displayStatus is None:
                return 0.0
            status = self.displayStatus.get_status(eventtime)
            progress = status.get("progress", 0.0)
            if progress is None:
                return 0.0
            return max(0.0, min(1.0, float(progress)))
        except Exception:
            return 0.0

    def _get_virtual_sdcard_status(self, eventtime):
        try:
            if self.virtual_sdcard is None:
                return {}
            return self.virtual_sdcard.get_status(eventtime)
        except Exception:
            return {}

    def _get_virtual_progress_fraction(self, eventtime):
        status = self._get_virtual_sdcard_status(eventtime)
        try:
            progress = status.get("progress", 0.0)
            if progress is not None:
                progress = max(0.0, min(1.0, float(progress)))
                if progress > 0.0:
                    return progress
        except Exception:
            pass

        try:
            file_position = float(status.get("file_position", 0) or 0)
            file_size = float(status.get("file_size", 0) or 0)
            if file_size > 0.0 and file_position > 0.0:
                return max(0.0, min(1.0, file_position / file_size))
        except Exception:
            pass

        return 0.0

    def _is_virtual_sdcard_active(self, eventtime):
        status = self._get_virtual_sdcard_status(eventtime)
        try:
            return bool(status.get("is_active", False))
        except Exception:
            return False


    def _get_gcode_position(self, eventtime):
        try:
            if self.gcode_move is None:
                return None
            status = self.gcode_move.get_status(eventtime)
            pos = status.get("gcode_position", None)
            if pos is None or len(pos) < 4:
                return None
            return [float(pos[0]), float(pos[1]), float(pos[2]), float(pos[3])]
        except Exception:
            return None

    def _reset_first_layer_detector(self):
        self.first_layer_print_detected = False
        self._print_detect_last_e = None
        self._print_detect_extruded = 0.0

    def _is_printing_started_automatically(self, eventtime, print_status):
        # Do NOT use virtual_sdcard.progress/file_position as the main trigger.
        # That moves during start G-code and switches too early.
        #
        # We switch Preparing -> Printing only after real positive extrusion
        # appears on first-layer Z range.
        if self.first_layer_print_detected:
            return True

        pos = self._get_gcode_position(eventtime)
        if pos is None:
            # Last-resort fallback only, to avoid being stuck forever if gcode_move
            # is unavailable on a modified Klipper build.
            display_progress = self._get_display_progress_fraction(eventtime)
            virtual_progress = self._get_virtual_progress_fraction(eventtime)
            if max(display_progress, virtual_progress) >= 0.020:
                self.first_layer_print_detected = True
                return True
            return False

        z = pos[2]
        e = pos[3]

        if self._print_detect_last_e is None:
            self._print_detect_last_e = e
            return False

        de = e - self._print_detect_last_e
        self._print_detect_last_e = e

        # Count only positive extrusion. Ignore retracts, G92 E0 resets and negative jumps.
        if 0.0 < de < 25.0:
            self._print_detect_extruded += de

        # First-layer zone. This is intentionally wide enough for normal first layer,
        # adaptive first layer and purge/prime at bed level, but it will not trigger
        # during heating/homing/travel.
        first_layer_z_min = 0.02
        first_layer_z_max = 1.20

        # Enough extrusion to avoid switching on tiny E noise.
        extrusion_threshold = 0.60

        if (
            first_layer_z_min <= z <= first_layer_z_max and
            self._print_detect_extruded >= extrusion_threshold
        ):
            self.first_layer_print_detected = True
            return True

        return False


    def _is_print_file_active(self, eventtime):
        status = self._get_virtual_sdcard_status(eventtime)
        try:
            if bool(status.get("is_active", False)):
                return True
        except Exception:
            pass

        try:
            file_path = status.get("file_path", None)
            if file_path:
                return True
        except Exception:
            pass

        return False

    def _has_print_context(self, eventtime, print_state):
        # True only when a real file print session has started.
        # This prevents Pause_Status / Pause_Attention_Status from triggering
        # during manual filament loading, manual extrusion, maintenance, etc.
        if print_state == "printing":
            return True

        if self.print_session_active:
            return True

        if self._is_print_file_active(eventtime):
            return True

        return False


    def _update_state(self):
        eventtime = self.reactor.monotonic()
        new_state = "idle"
        print_state = ""
        print_status = {}

        if self.print_stats:
            print_status = self.print_stats.get_status(eventtime)
            print_state = print_status.get('state', '')

            # Start/keep a print session only from actual file-print context.
            if print_state == "printing" or self._is_print_file_active(eventtime):
                self.print_session_active = True

            if print_state == "complete":
                if self.door_open:
                    self.print_completed = False
                    self.completed_exited = True
                else:
                    if not self.completed_exited:
                        self.print_completed = True

            elif self._is_pause_active(eventtime, print_state):
                # Pause effects are valid only during an active print session.
                # Manual filament load/extrude in idle must not trigger pause LEDs.
                if self._has_print_context(eventtime, print_state):
                    if self.filament_attention_latched or self._is_filament_attention_active(eventtime, latch=True):
                        new_state = "pause_attention"
                    else:
                        new_state = "pause"
                else:
                    new_state = "idle"

            elif print_state == "printing":
                self.print_completed = False
                self.completed_exited = False

                # During active printing, remember even a short filament/tangle fault.
                # Klipper can enter PAUSE after the sensor has already returned to normal.
                self._is_filament_attention_active(eventtime, latch=True)

                if self._is_printing_started_automatically(eventtime, print_status):
                    new_state = "printing"
                else:
                    new_state = "preparing"

        if self.print_completed and not self.door_open and not self.completed_exited:
            new_state = "completed"
        elif self.state == "completed" and self.door_open:
            new_state = "idle"
            self.print_completed = False
            self.completed_exited = True

        # Reset transient print-session detector only when the print is truly over.
        if new_state == "completed":
            self.print_session_active = False
            self.filament_attention_latched = False
            self.filament_attention_last_reason = ""
            self._reset_first_layer_detector()
        elif new_state == "idle" and not self._is_print_file_active(eventtime) and print_state not in ("printing", "paused"):
            self.print_session_active = False
            self.filament_attention_latched = False
            self.filament_attention_last_reason = ""
            self._reset_first_layer_detector()
        elif new_state == "printing":
            self.filament_attention_latched = False
            self.filament_attention_last_reason = ""

        if new_state != self.state:
            self.state = new_state
            self._update_led_effects()


    def _update_led_effects(self):
        for effect in self.effects:
            should_run = (
                (self.state == "idle" and effect.run_on_idle) or
                (self.state == "pause" and effect.run_on_pause) or
                (self.state == "pause_attention" and effect.run_on_pause_attention) or
                (self.state == "preparing" and effect.run_on_preparing) or
                (self.state == "printing" and effect.run_on_progress) or
                (self.state == "completed" and effect.run_on_completed)
            )

            if should_run and not effect.enabled:
                effect.set_fade_time(0.0)
                effect.set_enabled(True)
            elif not should_run and effect.enabled:
                effect.set_fade_time(0.0)
                effect.set_enabled(False)


    def _transmit_chain(self, chain):
        # Force update (dotstar workaround)
        if hasattr(chain, "prev_data"):
            chain.prev_data = None

        helper = getattr(chain, 'led_helper', None)
        if helper is None:
            raise RuntimeError("Klipper version not compatible: chain has no 'led_helper'.")

        # Request a transmit
        helper.need_transmit = True

        if hasattr(helper, '_check_transmit'):
            helper._check_transmit()
        elif hasattr(helper, 'check_transmit'):
            # Older Klipper / Kalico API
            helper.check_transmit(None)
        else:
            raise RuntimeError("Klipper version not compatible: led_helper missing '_check_transmit' and 'check_transmit'.")

    def _handle_shutdown(self):
        self.shutdown = True
        for effect in self.effects:
            if not effect.run_on_error:
                for chain in self.ledChains:
                    chain.led_helper.set_color(None, (0.0, 0.0, 0.0, 0.0))
                    self._transmit_chain(chain)
        pass
    
        

    def addEffect(self, effect):
        if effect in self.effects:
            self.effects.remove(effect)
        self.effects.append(effect)


    def _pollProgress(self, eventtime):
        status = self.displayStatus.get_status(eventtime)
        p = status.get('progress')
        if p is not None:
            self.printProgress = int(p * 100)
        self._update_state()
        return eventtime + 1

    def _apply_gamma(self, value):
        if value <= 0.0:
            return 0.0
        if value >= 1.0:
            return 1.0
        
        idx = value * (GAMMA_SIZE - 1)
        idx_low = int(idx)
        idx_high = min(idx_low + 1, GAMMA_SIZE - 1)
        weight = idx - idx_low
        
        return GAMMA_TABLE[idx_low] * (1 - weight) + GAMMA_TABLE[idx_high] * weight
 

    def _getColorData(self, colors, fade):
        clamp = (lambda x : 0.0 if x < 0.0 else 1.0 if x > 1.0 else x)
        colors = [x*clamp(fade) for x in colors]
        colors=colors + [0.0] * (4 - len(colors))
        colors=colors[:4]
        colors = [clamp(x) for x in colors]
        corrected = [self._apply_gamma(c) if i < 3 else c for i, c in enumerate(colors)]
        return tuple(corrected)

    def _getFrames(self, eventtime):
        chainsToUpdate = set()

        frames = [(effect, effect.getFrame(eventtime)) for effect in self.effects]

        #first set all LEDs to 0, that should be updated
        for effect, (frame, update) in frames:
            if update:
                for i in range(effect.ledCount):
                    chain,index=effect.leds[i]
                    chain.led_helper.led_state[index] = (0.0, 0.0, 0.0, 0.0)
                    chainsToUpdate.add(chain)

        #then sum up all effects for that LEDs
        for effect, (frame, update) in frames:
            if update:
                for i in range(effect.ledCount):
                    chain,index=effect.leds[i]
                    
                    current_state=list(chain.led_helper.led_state[index])
                    effect_state=self._getColorData(frame[i*COLORS:i*COLORS+COLORS], 
                                                    effect.fadeValue)

                    next_state=[min(1.0,a+b) for a,b in \
                                 zip(current_state, effect_state)]

                    chain.led_helper.led_state[index] = tuple(next_state)
                    chainsToUpdate.add(chain)

        if self.on_off_pin is not None:
            try:
                status = self.on_off_pin.get_status(eventtime)
                if status['value'] <= 0:
                    for effect, (frame, update) in frames:
                        if update and not effect.ignore_on_off_pin:
                            for i in range(effect.ledCount):
                                chain, index = effect.leds[i]
                                chain.led_helper.led_state[index] = (0.0, 0.0, 0.0, 0.0)
            except Exception as e:
                pass

        for chain in chainsToUpdate:
            if not self.shutdown: 
                self._transmit_chain(chain)
        if self.effects:
            next_eventtime=min(self.effects, key=lambda x: x.nextEventTime)\
                            .nextEventTime
        else:
            next_eventtime = eventtime
        # run at least with 10Hz
        next_eventtime=min(next_eventtime, eventtime + 0.1) 
        return next_eventtime
    
    def parse_chain(self, chain):
        chain = chain.strip()
        leds=[]
        parms = [parameter.strip() for parameter in chain.split()
                    if parameter.strip()]
        if parms:
            chainName=parms[0].replace(':',' ')
            ledIndices   = ''.join(parms[1:]).strip('()').split(',')
            for led in ledIndices:
                if led:
                    if '-' in led:
                        start, stop = map(int,led.split('-'))
                        if stop == start:
                            ledList = [start-1]
                        elif stop > start:
                            ledList = list(range(start-1, stop))
                        else:
                            ledList = list(reversed(range(stop-1, start)))
                        for i in ledList:
                            leds.append(int(i))
                    else:
                        for i in led.split(','):
                            leds.append(int(i)-1)

            return chainName, leds
        else:
            return None, None

    def cmd_STOP_LED_EFFECTS(self, gcmd):
        ledParam = gcmd.get('LEDS', "")
        stopAll = (ledParam == "")

        for effect in self.effects:
            stopEffect = stopAll
            if not stopAll:
                try:
                    chainName, ledIndices = self.parse_chain(ledParam)
                    chain = self.printer.lookup_object(chainName)
                except Exception as e:
                    raise gcmd.error("Unknown LED '%s'" % (ledParam,))

                if ledIndices == [] and chain in effect.ledChains: 
                    stopEffect = True
                else:
                    for index in ledIndices:
                        if (chain,index) in effect.leds: 
                            stopEffect=True

            if stopEffect:
                if effect.enabled:
                    effect.set_fade_time(gcmd.get_float('FADETIME', 0.0))
                effect.set_enabled(False)

def load_config(config):
    return ledFrameHandler(config)

######################################################################
# LED Effect
######################################################################

class ledEffect:
    def __init__(self, config):
        self.config       = config
        self.printer      = config.get_printer()
        self.gcode        = self.printer.lookup_object('gcode')
        self.gcode_macro  = self.printer.load_object(config, 'gcode_macro')
        self.handler      = self.printer.load_object(config, 'led_effect')
        self.frameRate    = 1.0 / config.getfloat('frame_rate', 
                                        default=24, minval=1, maxval=60)
        self.enabled      = False
        self.layers       = []
        self.fadeValue    = 0.0
        self.fadeTime     = 0.0
        self.fadeEndTime  = 0

        #Basic functions for layering colors. t=top and b=bottom color
        self.blendingModes  = {
            'top'       : (lambda t, b: t ),
            'bottom'    : (lambda t, b: b ),
            'add'       : (lambda t, b: t + b ),
            'subtract'  : (lambda t, b: (b - t) * (b - t > 0)),
            'subtract_b': (lambda t, b: (t - b) * (t - b > 0)),
            'difference': (lambda t, b: (t - b) * (t > b) + (b - t) * (t <= b)),
            'average'   : (lambda t, b: 0.5 * (t + b)),
            'multiply'  : (lambda t, b: t * b),
            'divide'    : (lambda t, b: t / b if b > 0 else 0 ),
            'divide_inv': (lambda t, b: b / t if t > 0 else 0 ),
            'screen'    : (lambda t, b: 1.0 - (1.0-t)*(1.0-b) ),
            'lighten'   : (lambda t, b: t * (t > b) +  b * (t <= b)),
            'darken'    : (lambda t, b: t * (t < b) +  b * (t >= b)),
            'overlay'   : (lambda t, b: \
                                2.0 * t * b if t > 0.5 else \
                                1.0 - (2.0 * (1.0-t) * (1.0-b)))
           }

        self.name         = config.get_name().split()[1]

        self.autoStart    = config.getboolean('autostart', False)
        self.run_on_error = config.getboolean('run_on_error', False)
        self.ignore_on_off_pin = config.getboolean('ignore_on_off_pin', False)
        self.layerTempl   = self.gcode_macro.load_template(config, 'layers')
        self.configLayers = []
        self.configLeds   = config.get('leds')

        self.nextEventTime = 0
        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.gcode.register_mux_command('SET_LED_EFFECT', 'EFFECT', self.name,
                                         self.cmd_SET_LED_EFFECT,
                                         desc=self.cmd_SET_LED_EFFECT_help)


        self.run_on_idle = config.getboolean('run_on_idle', False)
        self.run_on_pause = config.getboolean('run_on_pause', False)
        self.run_on_pause_attention = config.getboolean('run_on_pause_attention', False)
        self.run_on_progress = config.getboolean('run_on_progress', False)
        self.run_on_preparing = config.getboolean('run_on_preparing', False)
        self.run_on_completed = config.getboolean('run_on_completed', False)

        if any([self.run_on_pause, self.run_on_pause_attention, self.run_on_progress,
                self.run_on_preparing, self.run_on_completed]):
            self.autoStart = False

    cmd_SET_LED_EFFECT_help = 'Starts or Stops the specified led_effect'

    def _handle_ready(self):
        self.configChains = self.configLeds.split('\n')
        self.ledChains    = []
        self.leds         = []
        self.enabled = self.autoStart
        if not self.enabled:
            self.nextEventTime = self.handler.reactor.NEVER
        self.printer.register_event_handler('klippy:shutdown', 
                                    self._handle_shutdown)
        #map each LED from the chains to the "pixels" in the effect frame
        for chain in self.configChains:
            chainName, ledIndices = self.handler.parse_chain(chain)
            if chainName is not None:
                ledChain = self.printer.lookup_object(chainName)

                #Add each discrete chain to the collection
                if ledChain not in self.ledChains:
                    self.ledChains.append(ledChain)

                if ledIndices == [] :
                    for i in range(ledChain.led_helper.led_count):
                        self.leds.append((ledChain, int(i)))
                else:
                    for led in ledIndices:
                        self.leds.append((ledChain, led))

        self.ledCount = len(self.leds)
        self.frame = [0.0] * COLORS * self.ledCount

        #enumerate all effects from the subclasses of _layerBase...
        self.availableLayers = {str(c).rpartition('.layer')[2]\
                                 .replace("'>", "")\
                                 .lower() : c
                                   for c in self._layerBase.__subclasses__()
                                   if str(c).startswith("<class")}
        self._generateLayers()

    def _generateLayers(self, context=None):
        self.layers = []
        if context is None:
            context = self.gcode_macro.create_template_context()
            context.update({'params': {}, 'rawparams': ''})
        self.configLayers = self.layerTempl.render(context)
        for layer in [line for line \
            in self.configLayers.split('\n') if line.strip()]:

            parms = [parameter.strip() for parameter \
                in layer.split() if parameter.strip()]

            if not parms[0] in self.availableLayers:
                raise self.printer\
                    .config_error("LED Effect '%s' in section '%s' is not a " \
                        "valid effect layer" % (parms[0], self.name))

            if not parms[3] in self.blendingModes:
                raise self.printer.config_error("Blending mode '%s' in section "
                     "'%s' is not a valid blending mode"\
                         % (parms[3], self.name))

            layer = self.availableLayers[parms[0]]

            pad = lambda x: x + [0.0] * (COLORS - len(x))
            convert = lambda s: float(s)
                
            try:
                palette="".join(parms[4:])                                      # join all elements of the list
                palette="".join(palette.split())                                # remove whitespaces
                palette=palette.strip(",")
                palette=palette.split("),(")                                    # split colors
                palette=[c.split(",") for c in palette]                         # split color components
                palette=[[convert(k.strip("()")) for k in c] for c in palette]  # convert to float
                for i in palette: 
                    if len(i) > COLORS: 
                        raise Exception(
                            "Color %s has too many elements." % (str(i),))
                palette=[pad(c) for c in palette]                               # pad to COLORS colors
                palette=[k for c in palette for k in c]                         # flatten list
            except Exception as e:
                raise self.printer.config_error(
                    "Error parsing palette in '%s' for layer \"%s\": %s"\
                        % (self.config.get_name(), parms[0], e,))
            self.layers.insert(0, layer(handler       = self,
                                        frameHandler  = self.handler,
                                        effectRate    = float(parms[1]),
                                        effectCutoff  = float(parms[2]),
                                        paletteColors = palette,
                                        frameRate     = self.frameRate,
                                        ledCount      = len(self.leds),
                                        blendingMode  = parms[3]))

        self.handler.addEffect(self)

    def getFrame(self, eventtime):
        if not self.enabled and self.fadeValue <= 0.0:
            if self.nextEventTime < self.handler.reactor.NEVER:
                # Effect has just been disabled. Set colors to 0 and update once.
                self.nextEventTime = self.handler.reactor.NEVER
                self.frame = [0.0] * COLORS * self.ledCount
                update = True
            else:
                update = False
        else:
            update = True
            if eventtime >= self.nextEventTime:
                self.nextEventTime = eventtime + self.frameRate

                self.frame = [0.0] * COLORS * self.ledCount
                for layer in self.layers:
                    layerFrame = layer.nextFrame(eventtime)

                    if layerFrame:
                        blend = self.blendingModes[layer.blendingMode]
                        self.frame = [blend(t, b) for t, b in zip(layerFrame, self.frame)]

                if (self.fadeEndTime > eventtime) and (self.fadeTime > 0.0):
                    remainingFade = ((self.fadeEndTime - eventtime) / self.fadeTime)
                else:
                    remainingFade = 0.0    

                self.fadeValue = 1.0-remainingFade if self.enabled else remainingFade

        return self.frame, update

    def set_enabled(self, state):
        if self.enabled != state:
            self.enabled = state
            self.nextEventTime = self.handler.reactor.NOW
            self.handler._getFrames(self.handler.reactor.NOW)
    
    def reset_frame(self):
        for layer in self.layers:
            layer.frameNumber = 0

    def set_fade_time(self, fadetime):
        self.fadeTime = fadetime
        self.fadeEndTime = self.handler.reactor.monotonic() + fadetime
        if self.fadeTime == 0.0:
            self.fadeValue = 0.0

    def cmd_SET_LED_EFFECT(self, gcmd):
        parmFadeTime = gcmd.get_float('FADETIME', 0.0)

        if gcmd.get_int('STOP', 0) >= 1:
            if self.enabled:
                self.set_fade_time(parmFadeTime)
            self.set_enabled(False)
        else:
            if gcmd.get_int('REPLACE',0) >= 1:
                for led in self.leds:
                    for effect in self.handler.effects:
                        if effect is not self and led in effect.leds:
                            if effect.enabled:
                                effect.set_fade_time(parmFadeTime)
                            effect.set_enabled(False)

            if not self.enabled:
                self.set_fade_time(parmFadeTime)
            if gcmd.get_int('RESTART', 0) >= 1:
                self.reset_frame()
            self.set_enabled(True)
    
    def get_status(self, eventtime):
        return {'enabled':self.enabled}

    def _handle_shutdown(self):
        self.set_enabled(self.run_on_error)

    

    ######################################################################
    # LED Effect layers
    ######################################################################

    # super class for effect animations. new animations should
    # inherit this and return 1 frame of [r, g, b] * <number of leds>
    # per call of nextFrame()
    class _layerBase(object):
        def __init__(self, **kwargs):
            self.handler         = kwargs['handler']
            self.frameHandler    = kwargs['frameHandler']
            self.ledCount        = kwargs['ledCount']
            self.paletteColors   = colorArray(COLORS, kwargs['paletteColors'])
            self.effectRate      = kwargs['effectRate']
            self.effectCutoff    = kwargs['effectCutoff']
            self.frameRate       = kwargs['frameRate']
            self.blendingMode    = kwargs['blendingMode']
            self.frameNumber     = 0
            self.thisFrame       = []
            self.frameCount      = 1

        def nextFrame(self, eventtime):
            if not self.frameCount:
                return [0] * COLORS * self.ledCount
            self.frameNumber += 1
            self.frameNumber = self.frameNumber * \
                ( self.frameNumber < self.frameCount )
            self.lastFrameTime = eventtime

            return self.thisFrame[self.frameNumber]


    # Current project layers only.
    # This plugin is reduced to the Bambu-style bed status strip used by mod.cfg.
    # Supported layers:
    #   - bambu_idle
    #   - bambu_flow
    #   - bambu_bar
    #   - bambu_double_flash
    #   - bambu_complete
    #   - bambu_pause_breathe
    #   - bambu_pause_attention

    # BambuLab H2-style status indicator layers.
    # idle: slow breathing
    # preparing: orange flow
    # printing: progress bar
    # error: red double flash
    # complete: green solid
    # pause: orange breathing
    # pause_attention: orange double flash


    class layerBambu_idle(_layerBase):
        def __init__(self, **kwargs):
            super(ledEffect.layerBambu_idle, self).__init__(**kwargs)
            if self.effectRate <= 0:
                self.effectRate = 5.6
            if self.effectCutoff <= 0:
                self.effectCutoff = 3.0

        def _color(self, index, default):
            if len(self.paletteColors) > index:
                return self.paletteColors[index]
            return default

        def _clamp(self, value, low=0.0, high=1.0):
            return max(low, min(high, value))

        def _mix(self, a, b, t):
            t = self._clamp(t)
            return [a[i] * (1.0 - t) + b[i] * t for i in range(COLORS)]

        def _smoothstep(self, x):
            x = self._clamp(x)
            return x * x * (3.0 - 2.0 * x)

        def _mode_params(self, default_brightness=0.015, default_gamma=0.85, default_floor=0.0):
            params = self._color(1, [default_brightness, default_gamma, default_floor, 0.0])

            brightness = self._clamp(params[0], 0.0, 1.0)

            local_gamma = default_gamma
            if len(params) > 1 and params[1] > 0.0:
                local_gamma = params[1]
            local_gamma = max(0.10, local_gamma)

            floor = default_floor
            if len(params) > 2:
                floor = params[2]
            floor = self._clamp(floor, 0.0, brightness)

            return brightness, local_gamma, floor

        def _scaled_color(self, color, brightness):
            return [self._clamp(color[i]) * brightness for i in range(COLORS)]

        def nextFrame(self, eventtime):
            self.frameNumber += 1

            # Independent Bambu-like Idle.
            #
            # Palette:
            #   color 0 = normalized color/tint
            #   color 1 = params: (max_brightness, local_gamma, min_brightness_floor)
            #
            # Example:
            #   bambu_idle 5.6 3.0 top (0.720, 0.920, 0.720),(0.015, 0.85, 0.000)
            #
            # Timing:
            #   rise  = fade
            #   top   = effectCutoff
            #   floor = third parameter from params color
            period = max(2.0, float(self.effectRate))
            top_hold = self._clamp(float(self.effectCutoff), 0.1, period - 0.40)

            transition_time = max(0.20, (period - top_hold) / 2.0)
            rise_time = transition_time
            fade_time = transition_time

            t = (self.frameNumber * self.frameRate) % period

            base_color = self._color(0, [0.720, 0.920, 0.720, 0.0])
            brightness, local_gamma, floor = self._mode_params(0.015, 0.85, 0.0)

            high = self._scaled_color(base_color, brightness)
            low = self._scaled_color(base_color, floor)

            if t < rise_time:
                phase = self._smoothstep(t / rise_time)
            elif t < rise_time + top_hold:
                phase = 1.0
            else:
                phase = 1.0 - self._smoothstep((t - rise_time - top_hold) / fade_time)

            # Local per-mode gamma before global output gamma.
            phase = pow(self._clamp(phase), local_gamma)

            color = self._mix(low, high, phase)
            return colorArray(COLORS, color * self.ledCount)


    class layerBambu_flow(_layerBase):
        def __init__(self, **kwargs):
            super(ledEffect.layerBambu_flow, self).__init__(**kwargs)
            if self.effectRate <= 0:
                self.effectRate = 0.90
            if self.effectCutoff <= 0:
                self.effectCutoff = 0.62
            self.start_time = None

        def _color(self, index, default):
            if len(self.paletteColors) > index:
                return self.paletteColors[index]
            return default

        def _clamp(self, value, low=0.0, high=1.0):
            return max(low, min(high, value))

        def _mix(self, a, b, t):
            t = self._clamp(t)
            return [a[i] * (1.0 - t) + b[i] * t for i in range(COLORS)]

        def _smoothstep(self, x):
            x = self._clamp(x)
            return x * x * (3.0 - 2.0 * x)

        def _scaled_color(self, color, brightness):
            return [self._clamp(color[i]) * self._clamp(brightness) for i in range(COLORS)]

        def _params(self):
            # New palette format:
            #   color 0 = wave color
            #   color 1 = background color
            #   color 2 = params: (wave_brightness, background_brightness, local_gamma)
            #
            # Example:
            #   bambu_flow 0.90 0.62 top (1.000,0.520,0.000),(1.000,0.300,0.000),(0.040,0.006,1.10)
            params = self._color(2, [0.040, 0.006, 1.10, 0.0])

            wave_brightness = self._clamp(params[0], 0.0, 1.0)

            background_brightness = 0.006
            if len(params) > 1:
                background_brightness = self._clamp(params[1], 0.0, wave_brightness)

            local_gamma = 1.10
            if len(params) > 2 and params[2] > 0.0:
                local_gamma = max(0.10, params[2])

            return wave_brightness, background_brightness, local_gamma

        def nextFrame(self, eventtime):
            self.frameNumber += 1

            # Bambu H2/H2D-like Preparing:
            # "Orange light scrolling flow".
            #
            # This layer has independent wave and background controls:
            #   color 0 = wave color
            #   color 1 = background color
            #   color 2 = (wave_brightness, background_brightness, local_gamma)
            #
            # effectRate   = flow speed
            # effectCutoff = flow width
            #
            # Motion is based on eventtime to avoid visible jitter from skipped frames.
            if self.start_time is None:
                self.start_time = eventtime

            elapsed = max(0.0, eventtime - self.start_time)

            speed = float(self.effectRate)
            width = self._clamp(float(self.effectCutoff), 0.30, 0.90)

            wave_color = self._color(0, [1.000, 0.520, 0.000, 0.0])
            background_color = self._color(1, [1.000, 0.300, 0.000, 0.0])

            wave_brightness, background_brightness, local_gamma = self._params()

            high = self._scaled_color(wave_color, wave_brightness)
            low = self._scaled_color(background_color, background_brightness)

            head = (elapsed * speed * 0.16) % 1.0

            frame = []
            for i in range(self.ledCount):
                x = float(i) / max(1.0, float(self.ledCount - 1))

                # Cyclic distance to one broad moving wave.
                d = abs(x - head)
                d = min(d, 1.0 - d)

                intensity = exp(-((d / width) ** 2) * 9.0)
                intensity = self._smoothstep(intensity)
                intensity = pow(self._clamp(intensity), local_gamma)

                color = self._mix(low, high, intensity)
                frame.extend(color)

            return colorArray(COLORS, frame)


    class layerBambu_bar(_layerBase):
        def __init__(self, **kwargs):
            super(ledEffect.layerBambu_bar, self).__init__(**kwargs)
            self.display_progress = None
            self.last_eventtime = None

        def _color(self, index, default):
            if len(self.paletteColors) > index:
                return self.paletteColors[index]
            return default

        def _clamp(self, value, low=0.0, high=1.0):
            return max(low, min(high, value))

        def _smoothstep(self, x):
            x = self._clamp(x)
            return x * x * (3.0 - 2.0 * x)

        def _mix(self, a, b, t):
            t = self._clamp(t)
            return [a[i] * (1.0 - t) + b[i] * t for i in range(COLORS)]

        def _scaled_color(self, color, brightness):
            return [self._clamp(color[i]) * self._clamp(brightness) for i in range(COLORS)]

        def _params(self):
            # Palette format:
            #   color 0 = filled/progress color
            #   color 1 = empty/background color
            #   color 2 = params: (filled_brightness, empty_brightness, edge_gamma)
            #
            # Layer fields:
            #   effectRate   = smoothing speed, fraction/second
            #   effectCutoff = edge width in LED
            params = self._color(2, [0.018, 0.000, 0.85, 0.0])

            filled_brightness = self._clamp(params[0], 0.0, 1.0)

            empty_brightness = 0.0
            if len(params) > 1:
                empty_brightness = self._clamp(params[1], 0.0, filled_brightness)

            edge_gamma = 0.85
            if len(params) > 2 and params[2] > 0.0:
                edge_gamma = max(0.10, params[2])

            return filled_brightness, empty_brightness, edge_gamma

        def _get_target_progress(self):
            return self._clamp(self.frameHandler.printProgress / 100.0)

        def _get_display_progress(self, eventtime, target_progress, smoothing_speed):
            # Explicit 0% must be fully empty.
            if target_progress <= 0.0005:
                self.display_progress = 0.0
                self.last_eventtime = eventtime
                return 0.0

            if self.display_progress is None:
                self.display_progress = target_progress
                self.last_eventtime = eventtime
                return self.display_progress

            if self.last_eventtime is None:
                self.last_eventtime = eventtime

            dt = max(0.0, min(0.25, eventtime - self.last_eventtime))
            self.last_eventtime = eventtime

            diff = target_progress - self.display_progress
            if abs(diff) < 0.0005:
                self.display_progress = target_progress
                return self.display_progress

            max_step = smoothing_speed * dt
            if abs(diff) <= max_step:
                self.display_progress = target_progress
            else:
                self.display_progress += max_step if diff > 0 else -max_step

            return self._clamp(self.display_progress)

        def nextFrame(self, eventtime):
            self.frameNumber += 1

            target_progress = self._get_target_progress()

            smoothing_speed = float(self.effectRate) if self.effectRate > 0 else 0.85
            edge_width = float(self.effectCutoff) if self.effectCutoff > 0 else 1.2
            edge_width = self._clamp(edge_width, 0.2, max(0.2, float(self.ledCount)))

            progress = self._get_display_progress(eventtime, target_progress, smoothing_speed)

            filled_color = self._color(0, [0.720, 0.920, 0.720, 0.0])
            empty_color = self._color(1, [0.000, 0.000, 0.000, 0.0])
            filled_brightness, empty_brightness, edge_gamma = self._params()

            filled = self._scaled_color(filled_color, filled_brightness)
            empty = self._scaled_color(empty_color, empty_brightness)

            # Hard endpoints.
            if target_progress <= 0.0005 and progress <= 0.0005:
                return colorArray(COLORS, empty * self.ledCount)

            if target_progress >= 0.9995 and progress >= 0.9995:
                return colorArray(COLORS, filled * self.ledCount)

            fill = progress * float(self.ledCount)

            frame = []
            for i in range(self.ledCount):
                # One-sided progress bar:
                # - LEDs fully before fill are filled.
                # - LEDs fully after fill are empty.
                # - Only a small boundary zone is anti-aliased.
                #
                # This prevents 90% from looking like 100% because the soft edge
                # does not glow far forward into the empty end of the strip.
                led_start = float(i)
                led_end = float(i + 1)

                if fill >= led_end:
                    t = 1.0
                elif fill <= led_start:
                    t = 0.0
                else:
                    # Fractional coverage inside the current boundary LED.
                    t = fill - led_start

                # Optional very local edge softening, limited to edge_width.
                # It affects only LEDs just behind the boundary, not the far empty end.
                behind = fill - led_end
                if 0.0 < edge_width and -edge_width < behind < 0.0:
                    local = self._clamp((behind + edge_width) / edge_width)
                    t = max(t, local * 0.18)

                t = self._smoothstep(t)
                t = pow(self._clamp(t), edge_gamma)

                color = self._mix(empty, filled, t)
                frame.extend(color)

            return colorArray(COLORS, frame)


    class layerBambu_double_flash(_layerBase):
        def __init__(self, **kwargs):
            super(ledEffect.layerBambu_double_flash, self).__init__(**kwargs)
            if self.effectRate <= 0:
                self.effectRate = 1.6

        def _color(self, index, default):
            if len(self.paletteColors) > index:
                return self.paletteColors[index]
            return default

        def _clamp(self, value, low=0.0, high=1.0):
            return max(low, min(high, value))

        def _mix(self, a, b, t):
            t = self._clamp(t)
            return [a[i] * (1.0 - t) + b[i] * t for i in range(COLORS)]

        def _mode_params(self, default_brightness=0.35, default_gamma=1.0):
            params = self._color(1, [default_brightness, default_gamma, 0.0, 0.0])
            brightness = self._clamp(params[0], 0.0, 1.0)
            local_gamma = params[1] if len(params) > 1 and params[1] > 0.0 else default_gamma
            return brightness, max(0.10, local_gamma)

        def _scaled_color(self, color, brightness):
            return [self._clamp(color[i]) * brightness for i in range(COLORS)]

        def nextFrame(self, eventtime):
            self.frameNumber += 1

            # Bambu-like fault: red double flash.
            # Palette:
            #   color 0 = normalized alert color
            #   color 1 = params: (brightness, local_gamma, reserved)
            period = max(0.8, float(self.effectRate))
            t = (self.frameNumber * self.frameRate) % period
            x = t / period

            pulse1 = exp(-((x - 0.18) / 0.045) ** 2)
            pulse2 = exp(-((x - 0.36) / 0.045) ** 2)
            intensity = self._clamp(max(pulse1, pulse2))

            base_color = self._color(0, [1.0, 0.0, 0.0, 0.0])
            brightness, local_gamma = self._mode_params(0.35, 1.0)

            intensity = pow(intensity, local_gamma)

            high = self._scaled_color(base_color, brightness)
            low = [0.0] * COLORS
            color = self._mix(low, high, intensity)

            return colorArray(COLORS, color * self.ledCount)


    class layerBambu_complete(_layerBase):
        def __init__(self, **kwargs):
            super(ledEffect.layerBambu_complete, self).__init__(**kwargs)

        def _color(self, index, default):
            if len(self.paletteColors) > index:
                return self.paletteColors[index]
            return default

        def _clamp(self, value, low=0.0, high=1.0):
            return max(low, min(high, value))

        def _scaled_color(self, color, brightness):
            return [self._clamp(color[i]) * self._clamp(brightness) for i in range(COLORS)]

        def _params(self):
            # Palette format:
            #   color 0 = normalized complete color
            #   color 1 = params: (brightness, reserved, reserved)
            #
            # Example:
            #   bambu_complete 0 0 top (0.000,1.000,0.180),(0.030,0.000,0.000)
            params = self._color(1, [0.030, 0.000, 0.000, 0.0])
            brightness = self._clamp(params[0], 0.0, 1.0)
            return brightness

        def nextFrame(self, eventtime):
            self.frameNumber += 1

            # Bambu H2/H2D-like Completed:
            # solid green stays on until user action.
            base_color = self._color(0, [0.000, 1.000, 0.180, 0.0])
            brightness = self._params()

            color = self._scaled_color(base_color, brightness)
            return colorArray(COLORS, color * self.ledCount)


    class layerBambu_pause_breathe(_layerBase):
        def __init__(self, **kwargs):
            super(ledEffect.layerBambu_pause_breathe, self).__init__(**kwargs)
            if self.effectRate <= 0:
                self.effectRate = 3.8
            if self.effectCutoff <= 0:
                self.effectCutoff = 0.0
            self.start_time = None

        def _color(self, index, default):
            if len(self.paletteColors) > index:
                return self.paletteColors[index]
            return default

        def _clamp(self, value, low=0.0, high=1.0):
            return max(low, min(high, value))

        def _mix(self, a, b, t):
            t = self._clamp(t)
            return [a[i] * (1.0 - t) + b[i] * t for i in range(COLORS)]

        def _scaled_color(self, color, brightness):
            return [self._clamp(color[i]) * self._clamp(brightness) for i in range(COLORS)]

        def _params(self):
            # Palette format:
            #   color 0 = normalized pause color
            #   color 1 = params: (max_brightness, local_gamma, min_brightness_floor)
            #
            # Example:
            #   bambu_pause_breathe 3.8 0 top (1.000,0.180,0.000),(0.022,1.35,0.004)
            params = self._color(1, [0.022, 1.35, 0.004, 0.0])

            brightness = self._clamp(params[0], 0.0, 1.0)

            local_gamma = 1.35
            if len(params) > 1 and params[1] > 0.0:
                local_gamma = max(0.10, params[1])

            floor = 0.0
            if len(params) > 2:
                floor = self._clamp(params[2], 0.0, brightness)

            return brightness, local_gamma, floor

        def nextFrame(self, eventtime):
            self.frameNumber += 1

            # Smooth pause breathing without temporal dithering.
            # For WS2812/NeoPixel low brightness, avoid fade-to-zero:
            # the lowest 8-bit levels are visibly stepped/flickery.
            if self.start_time is None:
                self.start_time = eventtime

            elapsed = max(0.0, eventtime - self.start_time)
            period = max(0.5, float(self.effectRate))

            x = (elapsed / period) % 1.0

            # 0 -> 1 -> 0 cosine breath.
            phase = 0.5 - 0.5 * cos(2.0 * pi * x)

            base_color = self._color(0, [1.000, 0.180, 0.000, 0.0])
            brightness, local_gamma, floor = self._params()

            # Higher gamma skips the problematic low range faster.
            phase = pow(self._clamp(phase), local_gamma)

            high = self._scaled_color(base_color, brightness)
            low = self._scaled_color(base_color, floor)

            color = self._mix(low, high, phase)
            return colorArray(COLORS, color * self.ledCount)


    class layerBambu_pause_attention(_layerBase):
        def __init__(self, **kwargs):
            super(ledEffect.layerBambu_pause_attention, self).__init__(**kwargs)
            if self.effectRate <= 0:
                self.effectRate = 1.0

        def _color(self, index, default):
            if len(self.paletteColors) > index:
                return self.paletteColors[index]
            return default

        def _clamp(self, value, low=0.0, high=1.0):
            return max(low, min(high, value))

        def _mix(self, a, b, t):
            t = self._clamp(t)
            return [a[i] * (1.0 - t) + b[i] * t for i in range(COLORS)]

        def _scaled_color(self, color, brightness):
            return [self._clamp(color[i]) * self._clamp(brightness) for i in range(COLORS)]

        def _params(self):
            # Palette format:
            #   color 0 = normalized attention color
            #   color 1 = params: (flash_brightness, background_brightness, local_gamma)
            #
            # Example:
            #   bambu_pause_attention 1.0 0 top (1.000,0.180,0.000),(0.020,0.000,1.00)
            params = self._color(1, [0.020, 0.0, 1.0, 0.0])

            flash_brightness = self._clamp(params[0], 0.0, 1.0)

            background_brightness = 0.0
            if len(params) > 1:
                background_brightness = self._clamp(params[1], 0.0, flash_brightness)

            local_gamma = 1.0
            if len(params) > 2 and params[2] > 0.0:
                local_gamma = max(0.10, params[2])

            return flash_brightness, background_brightness, local_gamma

        def nextFrame(self, eventtime):
            self.frameNumber += 1

            # Sensor/attention pause:
            # orange double flash, same timing family as Bambu error,
            # but orange and weaker than red fault.
            #
            # effectRate = full double-flash cycle in seconds
            period = max(0.45, float(self.effectRate))
            t = (self.frameNumber * self.frameRate) % period
            x = t / period

            pulse1 = exp(-((x - 0.18) / 0.045) ** 2)
            pulse2 = exp(-((x - 0.36) / 0.045) ** 2)
            intensity = self._clamp(max(pulse1, pulse2))

            base_color = self._color(0, [1.000, 0.180, 0.000, 0.0])
            flash_brightness, background_brightness, local_gamma = self._params()

            intensity = pow(intensity, local_gamma)

            high = self._scaled_color(base_color, flash_brightness)
            low = self._scaled_color(base_color, background_brightness)

            color = self._mix(low, high, intensity)
            return colorArray(COLORS, color * self.ledCount)


def load_config_prefix(config):
    return ledEffect(config)
