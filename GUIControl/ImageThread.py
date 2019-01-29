import numpy as np
import cv2
import sys
import matplotlib.pyplot as plt
from PyQt5.QtCore import QThread, pyqtSignal
sys.path.append('../Common')
from Constants import *
from MessageSocket import *

#Receive and process header and images
#Non concluding experiments
#   linearize = true : Revert gamma corection of Jpeg before merging
#   crf = True : Precalculate the camera response forDebevec merge
#It seems that the Debevec merge without the camera response (ie a linear response) gives the best result
#it seems also that Durand's Tonemap gives the best result
#Note: sharpness is useful for focusing
#Focus your lens to have the maximum sharpness  
 
class ImageThread (QThread):
    histoSignal = pyqtSignal([object,])   #Signal to the GUI display histo
    headerSignal = pyqtSignal([object,])  #Signal to the GUI display header
    sharpnessSignal = pyqtSignal([object,])  #Signal to the GUI display sharpness
    merge = MERGE_NONE
    sharpness = False
    saveToFile = False
    histos = False
    images = []
    shutters = []
    linearize = False
    def __init__(self):
        QThread.__init__(self)
        self.threadID = 1
        self.name = "ImgThread"
        self.window = None
        self.saveOn = False
        self.mergeMertens = cv2.createMergeMertens()
        self.mergeDebevec = cv2.createMergeDebevec()
        self.toneMap = None
        self.calibrateDebevec = cv2.createCalibrateDebevec()
        self.responseDebevec = None
        self.crf = False
        self.reduceFactor = 1;
#        self.lookUpTable = np.empty((1,256), np.uint8)
#        for i in range(256):
#            self.lookUpTable[0,i] = np.clip(pow(i / 255.0, self.gamma) * 255.0, 0, 255)
#Revert the gamma 
        self.lookUpTable = np.empty([256,1]).astype("uint8")
        for i in range (0,256) :
            self.lookUpTable[i:] = ((i/255.)**(1./0.45))*255
#Read the camera response file if exists -> not really useful            
        try:
            npz = np.load("crf.npz")
            self.responseDebevec = npz['crf'][()]
            self.crf = True
        except Exception as e:
            pass
        if self.crf :
            self.toneMap = cv2.createTonemapReinhard(1.5,0,0,0)
        else :
            self.toneMap = cv2.createTonemapReinhard()

   
    def calcHistogram(self, image) :
        histos = []
        for i in range(3):
            histo = cv2.calcHist([image],[i],None,[256],[0,256])
            histos.append(histo)
#        self.histoSignal.emit(histos)  #Display histo in QT
        self.displayHistogramOverImage(histos, image)
        
    def displayHistogramOverImage(self, histos, image) :
        figure = plt.figure()
        axe = figure.add_subplot(111)
        colors = ('b','g','r')
        for i,col in enumerate(colors):
            axe.plot(histos[i],color = col)
        axe.set_xlim([0,256])
        axe.get_yaxis().set_visible(False)
        figure.tight_layout()
        figure.canvas.draw()
        w, h  = figure.canvas.get_width_height()
        buf = np.fromstring ( figure.canvas.tostring_rgb(), dtype=np.uint8 ).reshape(h,w,3)
        ww = int(image.shape[0]/4)
        hh = int(h*ww/w)
        resized = cv2.resize(buf, dsize=(ww,hh), interpolation=cv2.INTER_CUBIC)
        image[:hh,:ww] = resized
            
    def processImage(self, header, jpeg):
        bracket = header['bracket']
        count = header['count']
        jpeg = np.frombuffer(jpeg, np.uint8,count = len(jpeg)) 
        image = cv2.imdecode(jpeg, 1)   #Jpeg decode
        if self.merge != MERGE_NONE and bracket != 0 : #Merge
            if self.linearize :
                image = cv2.LUT(image, self.lookUpTable)
            self.images.append(image)
            self.shutters.append(header['shutter'])
            if bracket != 1 :
                return
            else :
                if self.merge == MERGE_MERTENS:
                    image = self.mergeMertens.process(self.images)
                else :
                    if self.crf :
                        image = self.mergeDebevec.process(self.images, np.asarray(self.shutters,dtype=np.float32)/1000000., self.responseDebevec)
                    else:
                        image = self.mergeDebevec.process(self.images, np.asarray(self.shutters,dtype=np.float32)/1000000.)
                    image = self.toneMap.process(image)
                image = np.clip(image*255, 0, 255).astype('uint8')
                if self.saveOn :
                    cv2.imwrite(self.directory + "/image_%#05d.jpg" % count, image)
                self.images.clear()
                self.shutters.clear()
        else :
            if self.saveOn :
                file = open(self.directory + "/image_%#05d_%#02d.jpg" % (header['count'], header['bracket']),'wb')
                file.write(jpeg)
                file.close()
            if self.sharpness :
                sharpness = cv2.Laplacian(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
                cv2.putText(image, str(sharpness), (200,200), cv2.FONT_HERSHEY_SIMPLEX,3,(255,255,255),2)
        if self.histos :            
            self.calcHistogram(image)
        if self.reduceFactor != 1 :
            newShape = (int(image.shape[1]/self.reduceFactor),int(image.shape[0]/self.reduceFactor))
            image = cv2.resize(image, dsize=newShape, interpolation=cv2.INTER_CUBIC)            
        cv2.imshow("PiCamera", image)
        key = cv2.waitKey(1)

    def saveToFile(self, saveFlag, directory) :
        self.saveOn = saveFlag
        self.directory = directory
            
    def run(self):
        print('ImageThread started')
        print(self.crf)
        self.imageSock = None
        try:
            sock = socket.socket()
            sock.connect(('192.168.0.22', 8000))
            print('ImageThread connected')
            self.imageSock = MessageSocket(sock)
            while True:
                header = self.imageSock.receiveObject()
                if header == None :
                    print('Closed connection')
                    cv2.destroyAllWindows()
                    if self.imageSock != None:
                        self.imageSock.close()
                    break
                typ = header['type']
                if typ == HEADER_STOP:
                    break
                self.headerSignal.emit(header) #«display header info in GUI if necessary (count,...)
                if  typ == HEADER_IMAGE :
                    image = self.imageSock.receiveMsg()
                    self.processImage(header, image)
                if  typ == HEADER_HDR :
                    image = self.imageSock.receiveMsg()
                    self.processHdrImage(header, image)
                
        finally:
            print('ImageThread terminated')
            cv2.destroyAllWindows()
            if self.imageSock != None:
                self.imageSock.shutdown()
                self.imageSock.close()

#Experimental Receive a set of exposures
    def processHdrImage(self, header, jpeg):
        jpeg = np.frombuffer(jpeg, np.uint8,count = len(jpeg))
        file = open(self.directory + "/ldr_%#05d.jpg" % (header['shutter']) ,'wb')
        file.write(jpeg)
        file.close()
        image = cv2.imdecode(jpeg, 1)   #Jpeg decode
        cv2.imshow("PiCamera", image)
        cv2.waitKey(1)



        