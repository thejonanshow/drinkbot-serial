import os
import redis
import json
import serial
import sys
import time
import random
import string
from serial import SerialException
import newrelic.agent

from multiprocessing import Process

newrelic.agent.initialize()
newrelic.agent.register_application()
application = newrelic.agent.application()


class DrinkbotSerial:

    DEBUG = os.environ.get("DEBUG", False)

    redis_conn = redis.Redis(charset="utf-8", decode_responses=True)
    serialport = os.environ.get("SERIAL_PORT")
    name = None

    def __init__(self):
        if not self.DEBUG:
            try:
                self.serial = serial.Serial(self.serialport, 9600, timeout=0)
            except serial.SerialException as e:
                newrelic.agent.notice_error()
                print("Error, ", e)
                sys.exit(0)

            self._init_name()
        else:
            self.name = "Mock Motor"
            self.redis_conn.publish("drinkbot", json.dumps({"name": self.name}))
            print(f"{self.name} Online")

    def _read_line(self):
        lsl = len(b"\r")
        line_buffer = []
        while True:
            next_char = self.serial.read(1)
            if next_char == b"":
                break
            line_buffer.append(next_char)
            if len(line_buffer) >= lsl and line_buffer[-lsl:] == [b"\r"]:
                break
        return b"".join(line_buffer)

    def _read_lines(self):
        lines = []
        try:
            while True:
                line = self.read_line()
                if not line:
                    break
                self.serial.flush_input()
                lines.append(line)
            return lines

        except SerialException as e:
            newrelic.agent.notice_error()
            print("Error, ", e)
            return None

    def _init_name(self):
        # Check for existing name, we wait after sending command
        # before checking for response
        self.send_cmd("Name,?")
        time.sleep(2)
        response = self._read_lines()

        # Loop over responses and check for a name response
        for line in response:
            if line[:6] == "?Name,":
                command, name = line.split(",")
                if name != "":
                    self.name = name

        # No name set yet, so no response of response was blank
        # Generate a random name and assign
        if self.name == None:
            new_name = "".join(
                random.choices(string.ascii_letters + string.digits, k=16)
            )

            newrelic.agent.record_custom_event(
                "Name/Set",
                {"old_name": self.name, "new_name": new_name},
                application=application,
            )

            self.name = new_name
            self.send_cmd(f"Name,{self.name}")

            # wait before we return that we're ready to accept commands
            time.sleep(2)

        # Publish our new/current name
        self.redis_conn.publish("drinkbot", json.dumps({"name": self.name}))

        return self.name

    def send_cmd(self, cmd):
        buf = cmd + "\r"  # add carriage return

        if not self.DEBUG:
            try:
                self.serial.write(buf.encode("utf-8"))
                return True
            except SerialException as e:
                newrelic.agent.notice_error()
                print("Error, ", e)
                return None
        else:
            print(buf.encode("utf-8"))

    def listen_for_commands(self):
        pubsub = self.redis_conn.pubsub()
        pubsub.subscribe("drinkbot")

        for message in pubsub.listen():
            if message.get("type") == "message":
                data = json.loads(message.get("data"))

                if "name" in data and "command" in data and data["name"] == self.name:
                    if data["command"][:4] == "Read":
                        if not self.DEBUG:
                            response = self._read_lines()
                            self.redis_conn.publish(
                                "drinkbot",
                                json.dumps({"name": self.name, "lines": response}),
                            )
                        else:
                            print(json.dumps({"name": self.name, "lines": []}))
                            self.redis_conn.publish(
                                "drinkbot",
                                json.dumps({"name": self.name, "lines": []}),
                            )
                    else:
                        if data["command"][:5] == "Name,":
                            new_name = data["command"].split(",")[1]

                            newrelic.agent.record_custom_event(
                                "Name/Set",
                                {"old_name": self.name, "new_name": new_name},
                                application=application,
                            )

                            self.name = new_name

                        elif data["command"][:5] == "Find,":
                            newrelic.agent.record_custom_event(
                                "Blink/LED",
                                {"name": self.name},
                                application=application,
                            )
                        elif data["command"][:2] == "D,":
                            # Dispensing, add metric to New Relic
                            newrelic.agent.record_custom_metric(
                                f"{self.name}/Dispensed",
                                int(data["command"].split(",")[1]),
                                application,
                            )
                        self.send_cmd(data["command"])


if __name__ == "__main__":
    dbs = DrinkbotSerial()
    Process(target=dbs.listen_for_commands).start()
