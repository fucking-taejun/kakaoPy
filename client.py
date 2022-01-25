from socket import socket

import booking
import checkIn
import asyncio
import cryptoManager
import time
from packet import Packet
from bson import BSON as bson
import httpApi
import json
import struct
import writer
from chat import Chat
from channel import Channel


class Client:
    def __init__(self, device_name="DEVICE", device_uuid="REVWSUNFMQ=="):
        self.__sock: Socket
        self.__StreamReader: asyncio.StreamReader
        self.__StreamWriter: asyncio.StreamWriter
        self.__crypto: cryptoManager.CryptoManager
        self.__writer: writer.Writer
        self.__accessKey: str

        self.device_name = device_name
        self.device_uuid = device_uuid

        self.__packetID = 0

        self.__processingBuffer = b""
        self.__processingSize = 0

        self.packetDict = {}

    def postText(self, chatId, li, text, notice=False):
        httpApi.postText(chatId, li, text, notice,
                         self.__accessKey, self.device_uuid)

    async def __recvPacket(self):
        encryptedBuffer = b""
        currentPacketSize = 0

        while True:
            recv = await self.__StreamReader.read(256)

            if not recv:
                print(recv)
                self.loop.stop()
                break

            encryptedBuffer += recv

            if not currentPacketSize and len(encryptedBuffer) >= 4:
                currentPacketSize = struct.unpack(
                    "<I", encryptedBuffer[0:4])[0]

            if currentPacketSize:
                encryptedPacketSize = currentPacketSize+4

                if len(encryptedBuffer) >= encryptedPacketSize:
                    self.loop.create_task(self.__processingPacket(
                        encryptedBuffer[0:encryptedPacketSize]))
                    encryptedBuffer = encryptedBuffer[encryptedPacketSize:]
                    currentPacketSize = 0

    async def __processingPacket(self, encryptedPacket):
        encLen = encryptedPacket[0:4]
        IV = encryptedPacket[4:20]
        BODY = encryptedPacket[20:]

        self.__processingBuffer += self.__crypto.aesDecrypt(BODY, IV)

        if not self.__processingSize and len(self.__processingBuffer) >= 22:
            self.__processingSize = struct.unpack(
                "<I", self.__processingBuffer[18:22])[0] + 22

        if self.__processingSize:
            if len(self.__processingBuffer) >= self.__processingSize:
                p = Packet()
                p.readLocoPacket(
                    self.__processingBuffer[:self.__processingSize])

                self.loop.create_task(self.__onPacket(p))

                self.__processingBuffer = self.__processingBuffer[self.__processingSize:]

    async def __onPacket(self, packet):
        if packet.PacketID in self.packetDict:
            self.packetDict[packet.PacketID].set_result(packet)
            del self.packetDict[packet.PacketID]

        self.loop.create_task(self.onPacket(packet))
        
        body=packet.toJsonBody()

        if packet.PacketName == "MSG":
            chatId = body["chatLog"]["chatId"]

            if "li" in body:
                li = body["li"]
            else:
                li = 0

            channel = Channel(chatId, li, self.__writer)
            chat = Chat(channel, body)

            self.loop.create_task(self.onMessage(chat))

        if packet.PacketName == "NEWMEM":
            chatId = body["chatLog"]["chatId"]

            if "li" in body:
                li = body["li"]
            else:
                li = 0

            channel = Channel(chatId, li, self.__writer)
            self.loop.create_task(self.onJoin(packet, channel))

        if packet.PacketName == "DELMEM":
            chatId = body["chatLog"]["chatId"]

            if "li" in body:
                li = body["li"]
            else:
                li = 0

            channel = Channel(chatId, li, self.__writer)
            self.loop.create_task(self.onQuit(packet, channel))
        
        if packet.PacketName == "DECUNREAD":
            chatId = body["chatId"]

            channel = Channel(chatId, 0, self.__writer)
            self.loop.create_task(self.onRead(channel, body))

    async def onPacket(self, packet):
        pass

    async def onMessage(self, chat):
        pass

    async def onJoin(self, packet, channel):
        pass

    async def onQuit(self, packet, channel):
        pass
    
    async def onRead(self, channel, packet):
        pass

    async def __heartbeat(self):
        while True:
            await asyncio.sleep(180)
            PingPacket = Packet(0, 0,
                                "PING", 0, bson.encode({}))
            self.loop.create_task(self.__writer.sendPacket(PingPacket))

    async def __login(self, LoginId, LoginPw,):
        r = json.loads(httpApi.Login(
            LoginId, LoginPw, self.device_name, self.device_uuid))

        if r["status"] == -101:
            print("다른곳에 로그인 되있습니다.")
            print("로그인 되있는 PC에서 로그아웃 해주세요")

        elif r["status"] == -100:
            print("디바이스 등록이 안 되어 있습니다.")
            print("RegisterDevice.py를 실행해주세요")

        if r["status"] != 0:
            self.loop.stop()
            raise Exception(str(r))

        self.__accessKey = r["access_token"]
        # print(self.__accessKey)

        bookingData = booking.getBookingData().toJsonBody()

        print("Bringing check in data... ", end='')
        while True:
            check_in_data = get_check_in_data(booking_data["ticket"]["lsl"][0],
                                              booking_data["wifi"]["ports"][0]).to_json_body()
            if "host" in check_in_data:
                print("Finished")
                break
            else:
                await asyncio.sleep(random.randint(100, 150) / 100)

        checkInData = checkIn.getCheckInData(
            bookingData["ticket"]["lsl"][0],
            bookingData["wifi"]["ports"][0]).toJsonBody()

        self.__StreamReader, self.__StreamWriter = await asyncio.open_connection(checkInData["host"], int(checkInData["port"]))

        self.__crypto = cryptoManager.CryptoManager()
        self.__writer = writer.Writer(
            self.__crypto, self.__StreamWriter, self.packetDict)

        LoginListPacket = Packet(0, 0, "LOGINLIST", 0, bson.encode({
            "appVer": "3.1.4",
            "prtVer": "1",
            "os": "win32",
            "lang": "ko",
            "duuid": self.device_uuid,
            "oauthToken": self.__accessKey,
            "dtype": 1,
            "ntype": 0,
            "MCCMNC": "999",
            "revision": 0,
            "chatIds": [],
            "maxIds": [],
            "lastTokenId": 0,
            "lbk": 0,
            "bg": False,
        }))

        self.__StreamWriter.write(self.__crypto.getHandshakePacket())

        self.loop.create_task(self.__writer.sendPacket(LoginListPacket))

        self.loop.create_task(self.__recvPacket())
        self.loop.create_task(self.__heartbeat())

    def run(self, LoginId, LoginPw):
        self.loop = asyncio.get_event_loop()
        self.loop.create_task(self.__login(LoginId, LoginPw))
        self.loop.run_forever()
