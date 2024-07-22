#!/usr/bin/python3
import serial
import os
import time
import pynmea2
import requests
import logging
from argparse import ArgumentParser
from logging.handlers import RotatingFileHandler
from serial.serialutil import *
from tenacity import retry, wait_fixed
import threading
import subprocess

logger = logging.getLogger("Comet GPS Log")

gps_speed = None
gps_latitude = None
gps_longitude = None
gps_satellite_num = None
cellular_operator, cellular_rssi_signal, cellular_generation = None, None, None
temperatures_dict = {}
ttyUSB2 = "/dev/ttyUSB2"
ttyUSB1 = "/dev/ttyUSB1"
LOCATION_FILE_PATH = os.getenv('LOCATION_FILE_PATH', "/code/init/location.txt")
NEEDED_MESSAGES = 2
server_url = os.getenv('server_url', '172.16.25.99:5000')
agent_uid = os.environ["AGENT_UID"]


@retry(wait=wait_fixed(5))
def start_gps() -> None:
    # Open serial port connection to /dev/ttyUSB2
    try:
        ser = serial.Serial(
            ttyUSB2,
            baudrate=9600,
            timeout=2,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        logger.info(f"Opened serial port {ttyUSB2} succesfully")
    except SerialException:
        logger.exception(f"Failed to open serial port {ttyUSB2}")

    # Clean serial input/output buffer
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    # Configure the Quectel EG25-G GPS
    ser.write(b"AT+QGPSCFG\r")
    time.sleep(1)
    # Activate the Quectel EG25-G GPS
    ser.write(b"AT+QGPS=1\r")  #
    time.sleep(1)
    logger.info("Activated GPS succesfully")
    ser.close()


def get_cellular_provider() -> None:
    global cellular_operator, cellular_rssi_signal, cellular_generation

    while True:

        time.sleep(5.0)

        ser = serial.Serial(
            port=ttyUSB2,
            baudrate=115200,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            bytesize=serial.EIGHTBITS,
            timeout=5,
        )

        # commands types
        operator_commnad = b"AT+COPS?\r\n"
        rssi_level = b"AT+CSQ\r\n"
        generation = b"AT+QNWINFO\r\n"

        try:
            # getting cellular provider
            ser.write(operator_commnad)
            time.sleep(1)
            operator_ser = ser.readlines()[1].decode("utf-8").split(",")[2]
            cellular_operator = operator_ser[1:len(operator_ser) - 1]

            # getting cellular signal
            ser.write(rssi_level)
            time.sleep(1)
            rssi_ser = ser.readlines()[1].decode("utf-8").split(":")[1].split(
                ",")[0]
            cellular_rssi_signal = rssi_ser[1:]

            # getting cellular generation
            ser.write(generation)
            time.sleep(1)
            generation_ser = (
                ser.readlines()[1].decode("utf-8").split(":")[1].split(",")[0])
            cellular_generation = generation_ser[2:len(generation_ser) - 1]

            logger.info(
                f"CELLULAR : Operator - {cellular_operator}, Signal DBM - {cellular_rssi_signal}, Generation - {cellular_generation}"
            )

            # post device metrics
            ser.close()
            post_comet_metrics()

        except Exception as err:
            ser.close()
            print(f"Something went wtring - {err}")


def get_gps_data() -> None:
    global gps_latitude, gps_longitude, gps_speed, gps_satellite_num
    collected_messages = 0

    # Open serial port connection to /dev/ttyUSB1
    ser = serial.Serial(
        ttyUSB1,
        baudrate=9600,
        timeout=2,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )

    # Clean serial input/output buffer
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    bytebuffer = ""
    line = []

    while True:
        # Reading until GGA and VTG messages are received
        if collected_messages == NEEDED_MESSAGES:
            return

        rcv = ser.read()
        try:
            line.append(rcv.decode("utf-8"))
        except:
            time.sleep(0.15)
            continue

        if rcv == b"\n":
            line = "".join(line)
            try:

                gps_msg = pynmea2.parse(line.strip())
                MESSAGE_TYPE = gps_msg.__dict__["sentence_type"]
                # print(gps_msg.fields)
                # print(gps_msg)

                if MESSAGE_TYPE == "GGA":
                    # location message
                    reassign_location(gps_msg, MESSAGE_TYPE)
                    collected_messages += 1

                if MESSAGE_TYPE == "VTG":
                    # speed message
                    gps_speed = gps_msg.spd_over_grnd_kmph
                    logger.info(f"GPS speed: {gps_speed}")
                    collected_messages += 1

                else:
                    # logger.info(f"GPS delivered message type - {MESSAGE_TYPE}")
                    pass

            except KeyboardInterrupt:
                raise
            except Exception as err:
                logger.info(f"Could not read GPS message - {err}")
            line = []


def is_all_data_available() -> bool:
    return (cellular_operator != None and cellular_rssi_signal != None
            and cellular_generation != None and gps_speed != None
            and gps_latitude != None and gps_longitude != None
            and gps_satellite_num != None)


def post_comet_metrics() -> None:

    # if is_all_data_available():

    response = requests.post(
        f"https://{server_url}",
        json={
            "payload_type": "comet_payload",
            "agent_uid": agent_uid,
            "operator": cellular_operator,
            "rssi_signal": cellular_rssi_signal,
            "cellular_generation": cellular_generation,
            "gps_speed": gps_speed,
            "gps_latitude": gps_latitude,
            "gps_longitude": gps_longitude,
            "gps_satellite_num": gps_satellite_num,
            "device_temperatures": temperatures_dict
        },
    )
    if response.status_code != 200:
        logger.info(
            f"Failed to update backend server.  Response: {response.content}"
        )
    else:
        logger.info(f"Updated backend with device metrics")


def reassign_location(gps_msg, message_type):
    global gps_latitude, gps_longitude, gps_satellite_num

    # reading GPS connected satellite number
    gps_satellite_num = (gps_msg.num_sats
                         if gps_msg.num_sats != None and gps_msg.num_sats
                         and gps_msg.num_sats != 00 else 0)

    logger.info(f"GPS connected satellite: {gps_satellite_num} ")

    # reading GPS coordinates
    if gps_msg.latitude != 0 and gps_msg.longitude != 0:
        gps_latitude = gps_msg.latitude
        gps_longitude = gps_msg.longitude
        logger.info(
            f"GPS coordinates: lat - {gps_latitude} long - {gps_longitude}")
        # reset gps location
    else:
        logger.info(
            f"GPS delivered message type - {message_type} with no coordinates")
        gps_latitude = None
        gps_longitude = None


def post_gps_coordinates() -> None:
    # updating the host webserver location

    while True:

        time.sleep(1.0)

        if gps_latitude is not None and gps_longitude is not None:
            send_single_location(gps_latitude, gps_longitude) 
            send_location_to_platerec(gps_latitude, gps_longitude)
            


def send_single_location(lat, lng) -> None:
    # updating the location file
    location_file = open(LOCATION_FILE_PATH, "w")
    location_file.write(f"{lat} {lng}")
    location_file.close()

    # updating the agent
    try:
        response = requests.get(
            f"http://localhost:8355/gps/set",
            params={
                "latitude": lat,
                "longitude": lng
            },
        )

        if response.status_code != 200:
            logger.info(
                f"Failed to update local webservice.  Response: {response.status_code}"
            )
        else:
            logger.info(
                f"Updated GPS coordinates to {lat} {lng}"
            )
    except KeyboardInterrupt:
        raise
    except Exception as er:
        logger.exception(
            f"Failed to send GPS data to webservice - {er}")
        
        #send gps to plate recognizer
def send_location_to_platerec(lat, lng) -> None:
    with open(LOCATION_FILE_PATH, "w") as location_file:
     location_file.write(f"{lat} {lng}")

    # updating the plate recognizer agent
    try:
        response = requests.post(
            "http://localhost:8001/user-data/",
            json={
                "id": "Hik-Camera",
                "data": {
                    "latitude": lat,
                    "longitude": lng
                }
            }
        )

        if response.status_code != 200:
            logger.info(
                f"Failed to update local webservice.  Response: {response.status_code}"
            )
        else:
            logger.info(
                f"Updated GPS coordinates to {lat} {lng}"
            )
    except KeyboardInterrupt:
        raise
    except Exception as er:
        logger.exception(
            f"Failed to send GPS data to webservice - {er}")


def post_gps_speed() -> None:

    while True:

        time.sleep(5.0)

        if gps_latitude != None and gps_longitude != None and gps_speed != None:
            try:
                response = requests.post(
                    f"https://{server_url}/metrics/agent-metrics/",
                    json={
                        "payload_type": "traffic_payload",
                        "agent_uid": agent_uid,
                        "lat": gps_latitude,
                        "lng": gps_longitude,
                        "speed": gps_speed,
                    },
                )
                if response.status_code != 200:
                    logger.info(
                        f"Failed to update backend server.  Response: {response.content}"
                    )
                else:
                    logger.info(
                        f"Updated backend with GPS traffic logic payload")
            except KeyboardInterrupt:
                raise
            except Exception as er:
                logger.exception(f"Exception on update backend server. - {er}")


def collect_temperatures() -> None:

    global temperatures_dict

    while True:

        time.sleep(5.0)

        try:

            ps = subprocess.run(['ls', '/sys/class/thermal'],
                                stdout=subprocess.PIPE)

            ps1 = subprocess.run(['grep', 'thermal'],
                                 input=ps.stdout,
                                 stdout=subprocess.PIPE)

            ps2 = subprocess.run(['wc', '-l'],
                                 input=ps1.stdout,
                                 stdout=subprocess.PIPE)

            thermals_number = int(ps2.stdout.decode('utf-8'))

            for index in range(thermals_number):

                result = subprocess.run(
                    ['cat', f"/sys/class/thermal/thermal_zone{index}/temp"],
                    stdout=subprocess.PIPE)
                temp_res = float(
                    result.stdout.decode('utf-8').replace('\n', '')) / 1000

                result = subprocess.run(
                    ['cat', f"/sys/class/thermal/thermal_zone{index}/type"],
                    stdout=subprocess.PIPE)
                type_res = result.stdout.decode('utf-8').replace('\n', '')

                temperatures_dict[type_res] = temp_res

            logger.info(
                f"Device CPU Temperature - {temperatures_dict['CPU-therm']}")

        except Exception as err:
            temperatures_dict = {}
            logger.info(f"Failed collecting device temprature - {err}")


def init_parser(logger) -> None:
    parser = ArgumentParser(description="Comet GPS Update Daemon")

    parser.add_argument(
        "-f",
        "--foreground",
        action="store_true",
        default=False,
        help="Run the daemon program in the foreground.  Default=false",
    )

    parser.add_argument(
        "-l",
        "--log",
        action="store",
        default="/var/log/comet_gps.log",
        help="log file for daemon process",
    )

    options = parser.parse_args()

    logger.setLevel(logging.DEBUG)

    if options.foreground:
        handler = logging.StreamHandler()

    else:
        # add a rotating file handler
        handler = RotatingFileHandler(options.log,
                                      maxBytes=20 * 1024 * 1024,
                                      backupCount=3)

    fmt = logging.Formatter("%(asctime)-15s %(thread)d: %(message)s",
                            datefmt="%Y-%m-%dT%H:%M:%S")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    logger.info("Script initialized")
    return options


def pull_location() -> None:
    try:
        location_file = open(LOCATION_FILE_PATH, "r")
    except Exception:
        # creating one if does not exist
        location_file = open(LOCATION_FILE_PATH, "a+")

    res = location_file.readline().split(" ")
    if len(res) == 2:
        send_single_location(res[0], res[1])

    location_file.close()


if __name__ == "__main__":
    print(f"server_url: {server_url}")
    options = init_parser(logger)
    pull_location()
    start_gps()
    # this thread updates the local LPR agent with its location
    update_agent_thread = threading.Thread(target=post_gps_coordinates,
                                           args=(),
                                           daemon=True)

    # this thread updates our backend traffic logics
    post_traffic_thread = threading.Thread(target=post_gps_speed,
                                           args=(),
                                           daemon=True)

    # this thread collects device metrics
    collect_temps_thread = threading.Thread(target=collect_temperatures,
                                            args=(),
                                            daemon=True)

    # this thread updates our backend with all the device metrics
    post_device_metrics = threading.Thread(target=get_cellular_provider,
                                           args=(),
                                           daemon=True)

    update_agent_thread.start()
    post_traffic_thread.start()
    post_device_metrics.start()
    collect_temps_thread.start()

    while True:
        try:
            # keeping the main thread alive until KeyboardInterrupt
            get_gps_data()
            time.sleep(1.0)
        except KeyboardInterrupt:
            raise
        except Exception as err:
            logger.exception(f"Something went wrong - {err}")
