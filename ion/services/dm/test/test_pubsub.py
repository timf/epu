#!/usr/bin/env python

"""
@file ion/services/coi/test/test_datapubsub.py
@author Michael Meisinger
@brief test service for registering topics for data pub sub
"""

import logging
logging = logging.getLogger(__name__)
import time
from twisted.internet import defer
from twisted.trial import unittest
from magnet.container import Container
from magnet.spawnable import Receiver
from magnet.spawnable import spawn

from ion.core.base_process import BaseProcess
from ion.services.dm.pubsub import DataPubsubClient
from ion.test.iontest import IonTestCase
import ion.util.procutils as pu

from ion.resources.dm_resource_descriptions import PubSubTopic

proc = """
# This is a data processing function that takes a sample message, filters for
# out of range values, adds to an event queue, and computes a new sample packet
# where outlyers are zero'ed out
#print "In data process"
data = content['data']
resdata = []
messages = []
for (ts,samp) in data:
    if samp<0 or samp>100:
        messages.append(('topic_qcevent',{'event':(ts,'out_of_range',samp)}))
        samp = 0
    newsamp = samp * samp
    ds = (ts,newsamp)
    resdata.append(ds)

messages.append(('topic_qc',{'metadata':{},'data':resdata}))

#print "messages", messages
result = messages
"""

class PubSubTest(IonTestCase):
    """Testing service classes of resource registry
    """

    @defer.inlineCallbacks
    def setUp(self):
        yield self._start_container()
        services = [
            {'name':'datapubsub_registry','module':'ion.services.dm.datapubsub.pubsub_registry','class':'DataPubSubRegistryService'},
            {'name':'data_pubsub','module':'ion.services.dm.pubsub','class':'DataPubsubService'}
            ]

        self.sup = yield self._spawn_processes(services)


    @defer.inlineCallbacks
    def tearDown(self):
        yield self._stop_container()

    @defer.inlineCallbacks
    def test_pubsub(self):

        dpsc = DataPubsubClient(self.sup)
        
        topic = PubSubTopic.create_fanout_topic("topic1")
        
        topic_name = yield dpsc.define_topic(topic)
        logging.info('Service reply: '+str(topic_name))

        dc1 = DataConsumer()
        dc1_id = yield dc1.spawn()
        yield dc1.attach(topic.name)

        dmsg = self._get_datamsg({}, [1,2,1,4,3,2])
        yield self.sup.send(topic.name, 'data', dmsg)

        # Need to await the delivery of data messages into the (separate) consumers
        yield pu.asleep(1)

        self.assertEqual(dc1.receive_cnt, 1)

        # Create a second data consumer
        dc2 = DataConsumer()
        dc2_id = yield dc2.spawn()
        yield dc2.attach(topic.name)

        dmsg = self._get_datamsg({}, [1,2,1,4,3,2])
        yield self.sup.send(topic.name, 'data', dmsg, {})

        # Need to await the delivery of data messages into the (separate) consumers
        yield pu.asleep(1)

        self.assertEqual(dc1.receive_cnt, 2)
        self.assertEqual(dc2.receive_cnt, 1)

    @defer.inlineCallbacks
    def test_chainprocess(self):
        # This test covers a chain of three data consumer processes on three
        # topics. One process is an event-detector and data-filter, sending
        # event messages to an event queue and a new data message to a different
        # data queue


        dpsc = DataPubsubClient(self.sup)
        
        topic_raw = PubSubTopic.create_fanout_topic("topic_raw")
        topic_raw = yield dpsc.define_topic(topic_raw)

        topic_qc = PubSubTopic.create_fanout_topic("topic_qc")
        topic_qc = yield dpsc.define_topic(topic_qc)
        
        topic_evt = PubSubTopic.create_fanout_topic("topic_evt")
        topic_evt = yield dpsc.define_topic(topic_evt)

        dc1 = DataConsumer()
        dc1_id = yield dc1.spawn()
        yield dc1.attach(topic_raw.name)
#        dp = DataProcess(proc)
        #dc1.set_ondata(dp.get_ondata())
        
        # Create the object and pass it the topics
        #ep = event_process(topic_qc,topic_evt)
        #dc1.set_ondata(ep.run)
        dc1.set_ondata(e_process,topic_qc,topic_evt)
        

        dc2 = DataConsumer()
        dc2_id = yield dc2.spawn()
        yield dc2.attach(topic_qc.name)

        dc3 = DataConsumer()
        dc3_id = yield dc3.spawn()
        yield dc3.attach(topic_evt.name)

        # Create an example data message with time
        dmsg = self._get_datamsg({}, [(101,5),(102,2),(103,4),(104,5),(105,-1),(106,9),(107,3),(108,888),(109,3),(110,4)])
        yield self.sup.send(topic_raw.name, 'data', dmsg, {})

        # Need to await the delivery of data messages into the consumers
        yield pu.asleep(2)

        self.assertEqual(dc1.receive_cnt, 1)
        self.assertEqual(dc2.receive_cnt, 1)
        self.assertEqual(dc3.receive_cnt, 2)

        dmsg = self._get_datamsg({}, [(111,8),(112,6),(113,4),(114,-2),(115,-1),(116,5),(117,3),(118,1),(119,4),(120,5)])
        yield self.sup.send(topic_raw.name, 'data', dmsg, {})

        # Need to await the delivery of data messages into the consumers
        yield pu.asleep(2)

        self.assertEqual(dc1.receive_cnt, 2)
        self.assertEqual(dc2.receive_cnt, 2)
        self.assertEqual(dc3.receive_cnt, 4)


    def _get_datamsg(self, metadata, data):
        #metadata.update('timestamp':time.clock())
        return {'metadata':metadata, 'data':data}


class event_process(object):
    
    def __init__(self,topic1,topic2):
        self.topic1 = topic1
        self.topic2 = topic2

    def run(self,content,headers):
        # This is a data processing function that takes a sample message, filters for   
        # out of range values, adds to an event queue, and computes a new sample packet
        # where outlyers are zero'ed out
        #print "In data process"
        data = content['data']
        resdata = []
        messages = []
        for (ts,samp) in data:
            if samp<0 or samp>100:
                messages.append((self.topic2,{'event':(ts,'out_of_range',samp)}))
                samp = 0
            newsamp = samp * samp
            ds = (ts,newsamp)
            resdata.append(ds)
        
        messages.append((self.topic1,{'metadata':{},'data':resdata}))
        
        #print "messages", messages
        result = messages
    

def e_process(content,headers,topic1,topic2):
    # This is a data processing function that takes a sample message, filters for   
    # out of range values, adds to an event queue, and computes a new sample packet
    # where outlyers are zero'ed out
    #print "In data process"
    data = content['data']
    resdata = []
    messages = []
    for (ts,samp) in data:
        if samp<0 or samp>100:
            messages.append((topic2,{'event':(ts,'out_of_range',samp)}))
            samp = 0
        newsamp = samp * samp
        ds = (ts,newsamp)
        resdata.append(ds)
    
    messages.append((topic1,{'metadata':{},'data':resdata}))
    
    #print "messages", messages
    return messages



class DataConsumer(BaseProcess):

    def set_ondata(self, ondata,topic1,topic2):
        self.ondata = ondata
        self.topic1 = topic1
        self.topic2 = topic2

    @defer.inlineCallbacks
    def attach(self, topic_name):
        yield self.init()
        self.dataReceiver = Receiver(__name__, topic_name)
        self.dataReceiver.handle(self.receive)
        self.dr_id = yield spawn(self.dataReceiver)
        logging.info("DataConsumer.attach "+str(self.dr_id)+" to topic "+str(topic_name))

        self.receive_cnt = 0
        self.received_msg = []
        self.ondata = None

    @defer.inlineCallbacks
    def op_data(self, content, headers, msg):
        logging.info("Data message received: "+repr(content))
        self.receive_cnt += 1
        self.received_msg.append(content)
        if hasattr(self, 'ondata') and self.ondata:
            logging.info("op_data: Executing data process")
            res = self.ondata(content, headers,self.topic1,self.topic2)
            logging.info("op_data: Finished data process")
            #print 'RES',res
            if res:
                for (topic, msg) in res:
                    #print 'TKTKTKTKTKTKTKT'
                    #print topic
                    #print msg
                    yield self.send(topic.name, 'data', msg, {})

class DataProcess(object):

    def __init__(self, procdef):
        self.proc_def = procdef

    def get_ondata(self):
        return self.execute_process

    def execute_process(self, content, headers):
        loc = {'content':content,'headers':headers}
        exec self.proc_def in globals(), loc
        if 'result' in loc:
            return loc['result']
        return None
