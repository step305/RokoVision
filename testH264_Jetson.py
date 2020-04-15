import os
import io
import time
import multiprocessing as mp
from queue import Empty
from PIL import Image
from http import server
import socketserver
import numpy as np
import cv2
import urllib.request

Ncams = 1
streamsVideo = ('http://192.168.0.20:8000', 'http://192.168.0.20:8000',
                'http://192.168.0.20:8000','http://192.168.0.20:8000', 'rtsp://admin:12345@192.168.0.200/mpeg4')
streamsRects = ('http://192.168.0.20:8000/data.html', 'http://192.168.0.20:8000/data.html',
                'http://192.168.0.20:8000/data.html', 'http://192.168.0.20:8000/data.html')

class objRect:
    def __init__(self, rect=None, side=None):
        if rect:
            self.x0 = rect[0]
            self.x1 = rect[1]
            self.y0 = rect[2]
            self.y1 = rect[3]
        else:
            self.x0 = 0
            self.x1 = 0
            self.y0 = 0
            self.y1 = 0
        if side:
            self.side = side
        else:
            self.side = 0 #0 = right cam, 1 = front cam, 2 = left cam, 3 = back cam
    
    def area(self):
        return (abs(self.x1-self.x0)*abs(self.y1-self.y0))
    
    def rect(self):
        return (self.x0, self.x1, self.y0, self.y1)
    
    def center(self):
        return (self.x1-self.x0, self.y1-self.y0)

    def height(self):
        return abs(self.y1-self.y0)
    
    def width(self):
        return abs(self.x1-self.x0)

    def setrect(self, rect):
        self.x0 = rect[0]
        self.x1 = rect[1]
        self.y0 = rect[2]
        self.y1 = rect[3]
    
    def copy(self):
        return objRect(self.rect(), self.side)
    
PAGE="""\
<html>
<head>
<title>Jetson TX2 image proccessing output</title>
</head>
<body>
<center><h1>Joined image</h1></center>
<center><img src="stream.mjpg" width="1740" height="740" /></center>
</body>
</html>
"""

class StreamingHandler(server.BaseHTTPRequestHandler):
    def do_GET(self):
        global cap
        if self.path == '/':
            self.send_response(301)
            self.send_header('Location', '/index.html')
            self.end_headers()
        elif self.path == '/index.html':
            stri = PAGE
            content = stri.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == '/stream.mjpg':
            self.send_response(200)
            self.send_header('Age', 0)
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            try:
                while True:
                        if not self.server.Queue.empty():
                            frame = self.server.Queue.get(False)
                            ret, buf = cv2.imencode('.jpg', frame)
                            frame = np.array(buf).tostring()
                            self.wfile.write(b'--FRAME\r\n')
                            self.send_header('Content-Type', 'image/jpeg')
                            self.send_header('Content-Length', len(frame))
                            self.end_headers()
                            self.wfile.write(frame)
                            self.wfile.write(b'\r\r')
            except Exception as e:
                logging.warning('Removed streaming client %s: %s', self.client_address, str(e))
        else:
            self.send_error(404)
            self.end_headers()

class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

def cam_reader(cam, queueoutVideo, queueinRect, stop):
    cap = cv2.VideoCapture(streamsVideo[cam])
    objdata = []
    no_detect_cnt = 0

    while cap:
        if stop.is_set():
            cap.release()
            break
        ret, frame = cap.read()
        if not ret:
            pass
#        print(frame.shape)
        if no_detect_cnt >= 25:
                objdata = []
        else:
            no_detect_cnt += 1
        if not queueinRect.empty():
            no_detect_cnt = 0
            objdata = queueinRect.get(False)
            for obj in objdata:
                [x0, y0, x1, y1] = obj['objcoord']
                x0 = int(x0*frame.shape[1])
                x1 = int(x1*frame.shape[1])
                y0 = int(y0*frame.shape[0])
                y1 = int(y1*frame.shape[0])
                frame = cv2.rectangle(frame, (x0,y0),(x1,y1), color=(0,255,0), thickness=3)
                frame = cv2.putText(frame, 'ID = {0:d}'.format(obj['objtype']), (x0+6,y1-6), cv2.FONT_HERSHEY_DUPLEX, 0.8, (255,0,0), 2)
        if not queueoutVideo.full():
            queueoutVideo.put((cam, frame))

def main_cam_reader(queueoutVideo, queueinRect, stop):
    cap = cv2.VideoCapture(streamsVideo[-1])
    objdata = []
    no_detect_cnt = 0

    while cap:
        if stop.is_set():
            cap.release()
            break
        ret, frame = cap.read()
        if not ret:
            pass
#        print(frame.shape)
        frame = cv2.resize(frame, (864, 486))
        if no_detect_cnt >= 25:
                objdata = []
        else:
            no_detect_cnt += 1
        if not queueinRect.empty():
            no_detect_cnt = 0
            objdata = queueinRect.get(False)
            for obj in objdata:
                [x0, y0, x1, y1] = obj['objcoord']
                x0 = int(x0*frame.shape[1])
                x1 = int(x1*frame.shape[1])
                y0 = int(y0*frame.shape[0])
                y1 = int(y1*frame.shape[0])
                frame = cv2.rectangle(frame, (x0,y0),(x1,y1), color=(0,255,0), thickness=3)
                frame = cv2.putText(frame, 'ID = {0:d}'.format(obj['objtype']), (x0+6,y1-6), cv2.FONT_HERSHEY_DUPLEX, 0.8, (255,0,0), 2)
        if not queueoutVideo.full():
            queueoutVideo.put((4, frame))

def RecognRect(cam, queueout, objsRectqueue, stop):
    dataresp = ''
    addr = streamsRects[cam]
    while not stop.is_set():
        try:
            response = urllib.request.urlopen(addr)
            dataresp += response.read().decode('utf-8')
            a = dataresp.find('ffffd9')
            b = dataresp.find('ffaaee')
            if a != -1 and b != -1:
                if b > (a+6):
                    str = dataresp[a+6:b]
                    strlist = str.split('\n')
                    objdata = []
                    objrects = []
                    #obj = {'objcoord':[0,0,0,0], 'objtype':0}
                    strr=''
                    for i in range(len(strlist)-1):
                        stri = strlist[i]
                        temp = re.findall(r'\d+', stri)
                        objtype = int(temp[-1])
                        temp = re.findall(r'\d+\.\d*', stri)
                        objcoord = map(float, temp)
                        objdata.append({'objcoord':objcoord, 'objtype':objtype})

                        objrects.append(objRect(objcoord, cam))
                    if objrects and not objsRectqueue.full():
                        objsRectqueue.put(objrects)
                    if objdata and queueout.empty():
                        queueout.put(objdata)
                dataresp = dataresp[b+6:]
        except:
            pass
        time.sleep(0.2)

def concat_frames(queueinVideo, queueout, stop):
    #logoImg = cv2.imread('time_replacer.png')
    frame_width = (420, 420, 420, 420, 864)
    frame_height = (234, 234, 234, 234, 486)
    HorGap = 20
    VerGap = 20
    big_frame = np.zeros((VerGap+frame_height[0]+frame_height[-1], 3*HorGap+4*frame_width[0], 3), np.uint8)
    big_frame[:] = (39, 27, 23)
    frame_coord_x = (0, frame_width[0]+HorGap, (frame_width[0]+HorGap)*2, (frame_width[0]+HorGap)*3, 0)
    frame_coord_y = (0, 0, 0, 0, frame_height[0] + VerGap)
    gs_pipeline = 'appsrc ! videoconvert ! omxh264enc control-rate=2 bitrate=1000000 ! ' \
                    'video/x-h264, stream-format=(string)byte-stream ! h264parse ! ' \
                    'rtph264pay mtu=1400 ! udpsink host=192.168.0.16 port=8001 sync=false async=false'
    vidstreader = cv2.VideoWriter(gs_pipeline, 0, 15/1, (big_frame.shape[1],big_frame.shape[0]), True)
    print(vidstreader)

    while not stop.is_set():
        if not queueinVideo.empty():
            (cam, frame) = queueinVideo.get(False)
            #big_frame[0:234, cam*420:(cam+1)*420, :] = frame
            big_frame[frame_coord_y[cam]:frame_coord_y[cam]+frame_height[cam], frame_coord_x[cam]:frame_coord_x[cam]+frame_width[cam]] = frame
            vidstreader.write(big_frame)
            #print(big_frame.shape)
            #if queueout.empty():
            #    queueout.put(big_frame)
    vidstreader.release()

def server_start(port, queue, stop):
    try:
        address = ('', port)
        server = StreamingServer(address, StreamingHandler)
        server.Queue = queueServer
        print('Server is running...')
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        stop.set()

if __name__ == '__main__':
    queueServer = mp.Queue(1)
    queueFrames = mp.Queue(5)
    queueGlobRecognRects = mp.Queue(10)

    StopFlag = mp.Event()

    queueRects = []
    procsDetectRects = []
    procsCamStream = []
    for cam in range(Ncams):
        queueRects.append(mp.Queue(1))
        procsDetectRects.append(mp.Process(target=RecognRect, args=(cam, queueRects[cam], queueGlobRecognRects, StopFlag)))
        procsCamStream.append(mp.Process(target=cam_reader, args=(cam, queueFrames, queueRects[cam], StopFlag)))
    queueRects.append(mp.Queue(1))
    procMainCamStream = mp.Process(target=main_cam_reader, args=(queueFrames, queueRects[-1], StopFlag))

    ConcatProc = mp.Process(target=concat_frames, args=(queueFrames, queueServer, StopFlag))
    ServerProc = mp.Process(target=server_start, args=(8000, queueServer, StopFlag))
    st = time.time()
    
    ConcatProc.start()
    ServerProc.start()
    for cam in range(Ncams):
        procsCamStream[cam].start()
        procsDetectRects[cam].start()
    procMainCamStream.start()

    while True:
        if StopFlag.is_set():
            StopFlag.set()
            time.sleep(0.1)
            for cam in range(Ncams):
                procsCamStream[cam].terminate()
                procsDetectRects[cam].terminate()
            procMainCamStream.terminate()
            ConcatProc.terminate()
            ServerProc.terminate()
            break
        time.sleep(1)
    exit(0)
