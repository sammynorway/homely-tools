#!/usr/bin/env python3

import time, sys, os, json, argparse, logging
import paho.mqtt.client as mqtt
from HomelyAPI import *
from MQTT_AD_Devices import *
from HomelyEntities import HomelyEntityRegistry

progname  = os.path.splitext(os.path.basename(sys.argv[0]))[0]
sleepfor  = 15 
alarm_previous_state = "in startup state"

# Set up root logger
logger = logging.getLogger(progname)
logging.basicConfig(stream=sys.stdout)

def alarmstates(state):
    return {
        'DISARMED':          'Disarmed',
        'ARM_PENDING':       'Disarmed',
        'ARM_STAY_PENDING':  'Armed stay',
        'ARMED_STAY':        'Armed stay',
        'ARM_NIGHT_PENDING': 'Armed night',
        'ARMED_NIGHT':       'Armed night',
        'ARM_AWAY_PENDING':  'Armed away',
        'ARMED_AWAY':        'Armed away',
        'BREACHED':          'Alarmed'
    }.get(state,"Error")

def alarmdomocodes(state):
    return {
        'DISARMED':          '0',
        'ARM_PENDING':       '0',
        'ARM_STAY_PENDING':  '0',
        'ARMED_STAY':        '0',
        'ARM_NIGHT_PENDING': '1',
        'ARMED_NIGHT':       '1',
        'ARM_AWAY_PENDING':  '2',
        'ARMED_AWAY':        '2'
    }.get(state,'')

def_user   = def_password =  ""
try:
	def_user = os.environ['HOMELY_USER']
except:
	pass
try:
	def_password = os.environ['HOMELY_PASSWORD']
except:pass

def_homename = ""
try: 
    def_homename = os.environ['HOMELY_HOME']
except:pass

def_discovery_prefix = "homeassistant"
try: 
    def_discovery_prefix = os.environ['MQTT_DISCOVERY']
except:pass

def_state_prefix = "homely"
try:
    def_state_prefix = os.environ['MQTT_STATE']
except:pass

def_mqtt_server="127.0.0.1"
try:
    def_mqtt_server = os.environ['MQTT_SERVER']
except:pass

def_mqtt_port=1883
try:
    def_mqtt_port = os.environ['MQTT_PORT']
except:pass

argp = argparse.ArgumentParser(prog=progname, description='Homely to MQTT Auto Discovery')
argp.add_argument('-u','--username',        default=def_user,                help="Homely username")
argp.add_argument('-p','--password',        default=def_password,            help="Homely password (only if you are alone on your system)") 
argp.add_argument('-l','--load',            default="",                      help="Load json state data from file") 
argp.add_argument('-s','--save',            default="",                      help="Save json state data to file") 
argp.add_argument(     '--home',            default=def_homename,            help="Home name in Homely") 
argp.add_argument(     '--siteprefix',      default="",                      help="Site prefix / home name") 
argp.add_argument(     '--domoticzurl',     default="",                      help="Homely password (only if you are alone on your system)") 
argp.add_argument(     '--sleep',           default=1200, type=int,          help="Sleep interval") 
argp.add_argument('-d','--debug',           action="store_true",             help="Debug")
argp.add_argument('-v','--verbose',         action="store_true",             help="Verbose")
argp.add_argument(     '--discoveryprefix', default=def_discovery_prefix,    help="MQTT HA discovery prefix")
argp.add_argument(     '--stateprefix',     default=def_state_prefix,        help="MQTT HA state prefix")
argp.add_argument(     '--mqttserver',      default=def_mqtt_server,         help="MQTT server name or address")
argp.add_argument(     '--mqttport',        default=def_mqtt_port, type=int, help="MQTT server port")
args=argp.parse_args()


if args.debug:
    logger.setLevel(logging.DEBUG)
elif args.verbose:
    logger.setLevel(logging.INFO)

h = HomelyAPI(logger=logger)

if args.domoticzurl != "":
    domoticz_settings = h.get("Domoticz settings", args.domoticzurl + '/json.htm?type=command&param=getsettings')
    domoticz_seccode  = domoticz_settings['SecPassword']
    logger.debug("Domoticz security setting is %s",domoticz_seccode)

if args.load != "":
    with open(args.load, "r") as rfile:
        hs = json.load(rfile)
        rfile.close()
    if args.verbose: print(json.dumps(hs)) 
else:
    if args.username != "":
        homely_user = args.username

    if args.password != "":
        homely_password = args.password

    if args.username == "" or args.password == "":
        argp.print_help();
        exit(1)

    token = h.login(args.username, args.password)
    token = h.tokenrefresh()

    print('------------------------- My home quewry ---------------------------')
    myhome = h.findhome(args.home)
    print('------------------------- My home result ---------------------------')
    print(myhome)

    print('------------------------- Home state ----------------------------------')
    hs = h.homestatus()
    if args.save != "":
        with open(args.save, "w") as wfile:
            json.dump(hs, wfile)
            wfile.close()

logger.debug("------------------------- Device dump -------------------------")
for i in hs['devices']:
    logger.debug(json.dumps(i))

print("------------------------- Device table -------------------------")
for d in hs['devices']:
    dev_unique_id = d['serialNumber']
    for feature in d['features'].keys():
        # if feature not in ['alarm', 'temperature']:
        #     continue
                # Hent diagnostikkdata dersom de finnes
        ds = d.get('features', {}).get('diagnostic', {}).get('states', {})

        # Håndter manglende nettverksinformasjon
        network_address = ds.get('networklinkaddress', {}).get('value', 'unknown')
        network_strength = ds.get('networklinkstrength', {}).get('value', 'unknown')

        # Enhetens tilkoblingsstatus
        online = 'online' if d.get('online', False) else 'offline'

        # Skriv enhetsinformasjon til loggen
        print(
            f"Serial {dev_unique_id} --> {d.get('modelName', 'Unknown')} "
            f"name {d.get('name', 'Unknown')}-{feature} "
            f"link state {online} "
            f"links to {network_address} "
            f"strength {network_strength}"
        )

if args.siteprefix != "":
    sitename = args.siteprefix
else:
    sitename = hs['name']

mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, progname)

# MQTT-autentisering
mqtt_user = os.environ.get("MQTT_USER")
mqtt_password = os.environ.get("MQTT_PASSWORD")

if mqtt_user:
    mq.username_pw_set(mqtt_user, mqtt_password)

mq.connect(args.mqttserver, port=args.mqttport, keepalive=600)
mq.loop_start()
hm = MQTT_AD_Config(
    sitename,
    mq, 
    discovery_topic = args.discoveryprefix,
    state_topic = args.stateprefix,
    logger = logger
)

main_alarm = MQTT_AD_Device("Gateway", "Alarm", "alarmstate", "selector")
main_alarm.device_config_append({ 
    "options" : [ "Disarmed", "Armed stay", "Armed night", "Armed away", "Alarmed", "Transitioning", "Error" ],
    "initial_option" : "Transitioning"
})
main_alarm.device_config_publish()
main_alarm.device_availability(True)

devices_lqi = MQTT_AD_Device("Gateway", "Minimum link quality", "links", "linkpercent")
devices_lqi.device_config_publish()
devices_lqi.device_availability(True)

devices_tamper = MQTT_AD_Device("Gateway", "Tamper switches", "tamper", "tamper")
devices_tamper.device_config_publish()
devices_tamper.device_availability(True)

devices_online = MQTT_AD_Device("Gateway", "Connectivities", "connectivity", "connectivity")
devices_online.device_config_publish()
devices_online.device_availability(True)

devices_lowbat = MQTT_AD_Device("Gateway", "Battery states", "battery", "battery")
devices_lowbat.device_config_publish()
devices_lowbat.device_availability(True)

def mqtt_device_factory(device, definition):
    return MQTT_AD_Device(
        device['name'],
        device['modelName'],
        definition.object_name,
        definition.component,
        device,
        entity_name=definition.display_name,
    )


entity_registry = HomelyEntityRegistry(mqtt_device_factory, logger=logger)


def feature_value(device, feature, state, default=None):
    return (
        device.get('features', {})
        .get(feature, {})
        .get('states', {})
        .get(state, {})
        .get('value', default)
    )


def publish_gateway_summary(home_state):
    devices = home_state.get('devices', [])
    devices_online.device_message('ON' if all(d.get('online', False) for d in devices) else 'OFF')

    low_battery = any(
        bool(feature_value(d, 'battery', 'low', False))
        or bool(feature_value(d, 'report', 'lowbat', False))
        for d in devices
    )
    devices_lowbat.device_message('ON' if low_battery else 'OFF')

    tampered = any(bool(feature_value(d, 'alarm', 'tamper', False)) for d in devices)
    devices_tamper.device_message('ON' if tampered else 'OFF')

    strengths = [
        feature_value(d, 'diagnostic', 'networklinkstrength')
        for d in devices
    ]
    strengths = [value for value in strengths if isinstance(value, (int, float))]
    if strengths:
        devices_lqi.device_json({'linkquality': min(strengths)})


def publish_home(home_state):
    entity_registry.publish_home(home_state)
    publish_gateway_summary(home_state)


# MQTT Discovery and initial state use the same definitions and value encoders
# as subsequent REST polls and WebSocket updates.
publish_home(hs)


def alarm_state(ht):
    global alarm_previous_state
    try:
        st = alarmstates(ht)
        if st != alarm_previous_state:
            logger.info(f"Homely alarm state change to {ht} (previous state {alarm_previous_state})")
            alarm_previous_state = st
            main_alarm.device_message(st)
            if args.domoticzurl != "":
                ds = alarmdomocodes(ht)
                logger.info(f"Homely alarm state is {ht} --> Domoticz security code {st}")
                dr = h.get("Domoticz set security state", args.domoticzurl + '/json.htm?type=command&param=setsecstatus&secstatus=' + ds + '&seccode=' + domoticz_seccode)
    except KeyError:
        pass  

#
# SocketIO message handler - running in separate thread
#
def siomsg(smsg):
    logger.info('websocket callback: %s', smsg)
    t = smsg['type']
    d = smsg['data']
    if t == 'device-state-changed':
        devid = d['deviceId']
        for c in d['changes']:
            entity_registry.publish_change(devid, c)
    elif t == 'alarm-state-changed': 
        alarm_state(d['state'])

alarm_state(hs['alarmState'])
h.startsio(siomsg)

while True:
    logger.info("Sleep for %d seconds ... ", sleepfor)

    sleepsec = 0
    #
    # Chop it into 5 sec intervals to check for socketio disconnects
    #
    while (sleepsec < sleepfor):
        time.sleep(5)
        sleepsec = sleepsec + 5
        siorc = h.sioexit()
        # Real token refresh is done only if needed - with a margin
        token = h.tokenrefresh()
        if siorc > 0:
            logger.error("SocketIO initiated exit ... ")
            sys.exit(siorc)

    sleepfor = args.sleep

    logger.info("Refreshing alarm status ... ")
    hs = h.homestatus()
    alarm_state(hs['alarmState'])
    publish_home(hs)
    
# vim:ts=4:sw=4
