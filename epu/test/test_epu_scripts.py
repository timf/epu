import StringIO
from collections import defaultdict
import os
import tempfile
import uuid
from ConfigParser import SafeConfigParser

from twisted.internet import defer, reactor, protocol
from twisted.internet.error import ProcessExitedAlready
import simplejson as json

from ion.core.process.service_process import ServiceProcess
from ion.test.iontest import IonTestCase
import ion.util.ionlog
import epu.util

log = ion.util.ionlog.getLogger(__name__)

class BaseEpuScriptTestCase(IonTestCase):
    timeout = 60

    def __init__(self, *args, **kwargs):
        self.processes = []
        self.sysname = str(uuid.uuid4())
        self.messaging_conf = None

        IonTestCase.__init__(self, *args, **kwargs)

    def run_script(self, script, args):
        script_path = os.path.join("scripts/", script)
        realargs = [script_path] + args
        epu_dir = os.path.realpath("..")
        processProtocol = EpuScriptProtocol()
        reactor.spawnProcess(processProtocol, script_path, env=os.environ,
                                    args=realargs, path=epu_dir)
        self.processes.append(processProtocol)
        return processProtocol

    @defer.inlineCallbacks
    def cleanup(self):
        for process in self.processes:
            if process.transport.pid:
                try:
                    process.transport.signalProcess("TERM")
                    yield process.deferred
                except ProcessExitedAlready:
                    pass
        if self.messaging_conf:
            os.unlink(self.messaging_conf)

    def write_messaging_conf(self):
        if self.messaging_conf:
            return self.messaging_conf
        
        config = SafeConfigParser()
        config.add_section(epu.util.KEY_MESSAGING)
        config.set(epu.util.KEY_MESSAGING, epu.util.KEY_BROKERHOST,
                   self.twisted_container_service.config['broker_host'])
        config.set(epu.util.KEY_MESSAGING, epu.util.KEY_SYSNAME,
                   self.sysname)

        fd, path = tempfile.mkstemp()
        f = None
        try:
            f = os.fdopen(fd, 'w')
            config.write(f)
            f.close()

        except Exception,e:
            if f:
                f.close()
            os.unlink(path)
            self.fail(e)
        else:
            log.debug("Wrote messaging config to %s", path)
            self.messaging_conf = path
            return path


class TestEpuKiller(BaseEpuScriptTestCase):
    @defer.inlineCallbacks
    def setUp(self):
        yield self._start_container(sysname=self.sysname)

    @defer.inlineCallbacks
    def tearDown(self):
        yield self.cleanup()
        yield self._shutdown_processes()
        yield self._stop_container()

    @defer.inlineCallbacks
    def test_killall_ok(self):
        p = FakeProvisioner()
        yield self._spawn_process(p)

        p.poll_count['terminate_all_rpc'] = 1

        args = [self.write_messaging_conf()]
        process_protocol = self.run_script("epu-killer", args)
        yield process_protocol.deferred

        self.assertEqual(len(p.requests), 1)
        self.assertEqual(len(p.requests['terminate_all_rpc']), 2)

# this one takes too long for message to timeout. some way to control that?
# note timeout occurs in epu-killer, so a different python process
#
#    @defer.inlineCallbacks
#    def test_killall_missing(self):
#
#        args = [self.write_messaging_conf()]
#        process_protocol = self.run_script("epu-killer", args)
#
#        try:
#            yield process_protocol.deferred
#        except EpuScriptError,e:
#            self.assertTrue(e.exitcode > 0)
#        else:
#            self.fail("Expected an error from epu-killer")

    @defer.inlineCallbacks
    def test_killall_error(self):

        p = FakeProvisioner()
        yield self._spawn_process(p)
        p.errors['terminate_all_rpc'] = Exception("BOOOOM!")
        p.poll_count['terminate_all_rpc'] = 1

        args = [self.write_messaging_conf()]
        process_protocol = self.run_script("epu-killer", args)

        try:
            yield process_protocol.deferred
        except EpuScriptError,e:
            self.assertTrue(e.exitcode > 0)
        else:
            self.fail("Expected an error from epu-killer")

        self.assertEqual(len(p.requests), 1)
        # 1 poll + 1 initial attempt + 5 error retries
        self.assertEqual(len(p.requests['terminate_all_rpc']), 7)


class TestEpuState(BaseEpuScriptTestCase):

    @defer.inlineCallbacks
    def setUp(self):
        yield self._start_container(sysname=self.sysname)

    @defer.inlineCallbacks
    def tearDown(self):
        yield self.cleanup()
        yield self._shutdown_processes()
        yield self._stop_container()

    @defer.inlineCallbacks
    def test_parallel_controllers(self):
        controller_names = ['TestEpuStateControllerA',
                            'TestEpuStateControllerB',
                            'TestEpuStateControllerC',
                            'TestEpuStateControllerD']
        controllers = []

        for controller_name in controller_names:
            c = FakeEpuController(spawnargs={'servicename': controller_name})
            c.fake_state = {"state" : controller_name}
            yield self._spawn_process(c)
            controllers.append(c)

        messaging_conf = self.write_messaging_conf()

        fd, output_path = tempfile.mkstemp()
        os.close(fd)
        try:

            args = [messaging_conf, output_path] + controller_names
            # this one will error but shouldn't ruin it for everyone else
            #args.append("NotARealController")
            process_protocol = self.run_script("epu-state", args)
            yield process_protocol.deferred

            in_file = None
            result = None
            try:
                in_file = open(output_path)
                result = json.load(in_file)
            finally:
                if in_file:
                    in_file.close()

            self.assertEqual(len(result), len(controllers))
            for controller_name in controller_names:
                self.assertIn(controller_name, result)
                self.assertEqual(result[controller_name], {'state': controller_name})


        finally:
            os.unlink(output_path)


class FakeProvisioner(ServiceProcess):
    declare = ServiceProcess.service_declare(name='provisioner',
                                          version='0.1.1',
                                          dependencies=[])

    def __init__(self, *args, **kwargs):
        self.requests = defaultdict(list)
        self.poll_count = {}
        self.errors = {}

        ServiceProcess.__init__(self, *args, **kwargs)

    def op_terminate_nodes(self, content, headers, msg):
        self.requests['terminate_nodes'].append(content)
        error = self.errors.get('terminate_nodes')
        if error:
            return defer.fail(error)
        return defer.succeed(None)

    def op_terminate_all_rpc(self, content, headers, msg):
        self.requests['terminate_all_rpc'].append(content)

        # return False a number of times before the real response.
        # to test looping behavior
        poll_count = self.poll_count.get('terminate_all_rpc')
        if poll_count:
            self.poll_count['terminate_all_rpc'] = poll_count-1
            return self.reply_ok(msg, False)

        error = self.errors.get('terminate_all_rpc')
        if error:
            return self.reply_err(msg, exception=error)
        return self.reply_ok(msg, True)


class FakeEpuController(ServiceProcess):

    declare = ServiceProcess.service_declare(name='fake_epu_controller',
                                          version='0.1.1',
                                          dependencies=[])
    def __init__(self, *args, **kwargs):
        self.fake_state = None

        ServiceProcess.__init__(self, *args, **kwargs)

    def op_whole_state(self, content, headers, msg):
        log.debug("Got whole_state request")
        return self.reply_ok(msg, self.fake_state)


class EpuScriptProtocol(protocol.ProcessProtocol):
    def __init__(self):
        self.s = StringIO.StringIO()
        self.deferred = defer.Deferred()

    def outReceived(self, data):
        self.s.write(data)
    def errReceived(self, data):
        self.s.write(data)
    def processExited(self, status):
        if status.value.exitCode:
            log.warn("process exited nonzero: %s", status.value.exitCode)
            log.warn("output: %s", self.s.getvalue())
            error = EpuScriptError(self.s.getvalue(),
                                   exitcode=status.value.exitCode)
            self.deferred.errback(error)
        else:
            log.info("process exited ok")
            log.info("======BEGIN EPU SCRIPT OUTPUT=====\n"+
                     "%s======END EPU SCRIPT OUTPUT=====", self.s.getvalue())
            self.deferred.callback(None)


class EpuScriptError(Exception):
    def __init__(self, msg, exitcode=None):
        self.exitcode = exitcode
        self.msg = msg
        s = "EPU script error. exitcode=%s msg=%s" % (exitcode,msg)
        Exception.__init__(self, s)
    