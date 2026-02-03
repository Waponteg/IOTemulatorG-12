import ssl

import json
import paho.mqtt.client as mqtt
from random import uniform
import time
import argparse

parser = argparse.ArgumentParser(description='IOT Sensor Emulator')
parser.add_argument("--host", type=str,
                    default="iotlab.virtual.uniandes.edu.co", help="MQTT Host")
parser.add_argument("--user", type=str, required=True, help="MQTT User")
parser.add_argument("--passwd", type=str, required=True, help="MQTT Password")
parser.add_argument("--city", type=str, required=True, help="MQTT City")

args = parser.parse_args()

client = mqtt.Client()


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✓ Conectado exitosamente al servidor MQTT")
    else:
        print(f"✗ Error de conexión: código {rc}")
    print(f"  Host: {args.host}, Usuario: {args.user}")


def on_disconnect(client, userdata, rc):
    if rc != 0:
        print(f"✗ Desconexión inesperada: código {rc}")


def on_publish(client, userdata, mid):
    print(f"  → Publicado (ID: {mid})")


client.tls_set(ca_certs='ca.crt',
               tls_version=ssl.PROTOCOL_TLSv1_2, cert_reqs=ssl.CERT_NONE)
client.tls_insecure_set(True)
client.username_pw_set(args.user, args.passwd)
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_publish = on_publish

try:
    print("Intentando conectar...")
    client.connect(args.host, 8082, 60)
    client.loop_start()  # ← IMPORTANTE: inicia el loop MQTT
except Exception as e:
    print(f"✗ Error de conexión: {e}")
    exit(1)


while True:
    topic1 = "temperatura/{}/{}".format(args.city, args.user)
    topic2 = "humedad/{}/{}".format(args.city, args.user)
    topic3 = "luminosidad/{}/{}".format(args.city, args.user)
    # topic1 = "temperatura/"
    # topic2 = "humedad/"
    value1 = float(round(uniform(10, 30), 1))
    value2 = float(round(uniform(50, 99), 1))
    value3 = int(round(uniform(0, 1023), 0))
    value1 = json.dumps({"value": value1})
    value2 = json.dumps({"value": value2})
    value3 = json.dumps({"value": value3})
    result1 = client.publish(topic1, value1)
    result2 = client.publish(topic2, value2)
    result3 = client.publish(topic3, value3)
    print(topic1 + ": " + value1)
    print(topic2 + ": " + value2)
    print(topic3 + ": " + value3)
    time.sleep(2)
