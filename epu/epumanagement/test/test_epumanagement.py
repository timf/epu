from twisted.internet import defer
from twisted.trial import unittest

from epu.decisionengine.impls.simplest import CONF_PRESERVE_N
from epu.epumanagement import EPUManagement
from epu.epumanagement.test.mocks import MockSubscriberNotifier, MockProvisionerClient, MockOUAgentClient
from epu.epumanagement.conf import *

import ion.util.ionlog

log = ion.util.ionlog.getLogger(__name__)

MOCK_PKG = "epu.epumanagement.test.mocks"

class EPUManagementBasicTests(unittest.TestCase):
    """
    Tests that cover basic things like running a decision engine cycle and making sure a VM
    is requested, etc.
    """

    def setUp(self):
        # Mock mode:
        initial_conf = {EPUM_INITIALCONF_PERSISTENCE: "memory",
                        EPUM_INITIALCONF_EXTERNAL_DECIDE: True}
        self.notifier = MockSubscriberNotifier()
        self.provisioner_client = MockProvisionerClient()
        self.ou_client = MockOUAgentClient()
        self.epum = EPUManagement(initial_conf, self.notifier, self.provisioner_client, self.ou_client)

        # For instance-state changes "from the provisioner"
        self.provisioner_client._set_epum(self.epum)

        # For heartbeats "from the OU instance"
        self.ou_client._set_epum(self.epum)

    def _config_mock1(self):
        """Keeps increment count
        """
        general = {EPUM_CONF_ENGINE_CLASS: MOCK_PKG + ".MockDecisionEngine01"}
        health = {EPUM_CONF_HEALTH_MONITOR: False}
        engine = {CONF_PRESERVE_N:1}
        return {EPUM_CONF_GENERAL:general, EPUM_CONF_ENGINE: engine, EPUM_CONF_HEALTH: health}

    def _config_mock2(self):
        """decide and reconfigure fail
        """
        conf = self._config_mock1()
        conf[EPUM_CONF_GENERAL] = {EPUM_CONF_ENGINE_CLASS: MOCK_PKG + ".MockDecisionEngine02"}
        return conf

    def _config_mock3(self):
        """uses Deferred
        """
        conf = self._config_mock1()
        conf[EPUM_CONF_GENERAL] = {EPUM_CONF_ENGINE_CLASS: MOCK_PKG + ".MockDecisionEngine03"}
        return conf

    def _config_simplest_epuconf(self, n_preserving):
        """Get 'simplest' EPU conf with specified NPreserving policy
        """
        engine_class = "epu.decisionengine.impls.simplest.SimplestEngine"
        general = {EPUM_CONF_ENGINE_CLASS: engine_class}
        health = {EPUM_CONF_HEALTH_MONITOR: False}
        engine = {CONF_PRESERVE_N:n_preserving}
        return {EPUM_CONF_GENERAL:general, EPUM_CONF_ENGINE: engine, EPUM_CONF_HEALTH: health}

    @defer.inlineCallbacks
    def test_engine_decide(self):
        """
        Verify decide is called at expected time
        """
        yield self.epum.initialize()
        epu_config = self._config_mock1()
        epu_name = "testing123"
        yield self.epum.msg_add_epu(None, epu_name, epu_config)
        yield self.epum._run_decisions()

        # digging into internal structure to get engine instances
        epu_engine = yield self.epum.decider.engines[epu_name]
        self.assertNotEqual(epu_engine, None)
        self.assertEqual(epu_engine.initialize_count, 1)
        self.assertEqual(epu_engine.initialize_conf[CONF_PRESERVE_N], 1)
        self.assertEqual(epu_engine.decide_count, 1)
        yield self.epum._run_decisions()
        self.assertEqual(epu_engine.decide_count, 2)

    @defer.inlineCallbacks
    def test_engine_reconfigure(self):
        """
        Verify reconfigure is called after a 'worker' alters the EPU config
        """
        yield self.epum.initialize()
        epu_config = self._config_mock1()
        epu_name1 = "testing123"
        epu_name2 = "testing789"
        yield self.epum.msg_add_epu(None, epu_name1, epu_config)
        yield self.epum.msg_add_epu(None, epu_name2, epu_config)
        yield self.epum._run_decisions()

        # digging into internal structure to get engine instances
        epu_engine1 = yield self.epum.decider.engines[epu_name1]
        epu_engine2 = yield self.epum.decider.engines[epu_name2]
        self.assertEqual(epu_engine1.decide_count, 1)
        self.assertEqual(epu_engine2.decide_count, 1)

        # reconfigure test
        self.assertEqual(epu_engine1.reconfigure_count, 0)
        self.assertEqual(epu_engine2.reconfigure_count, 0)
        epu_config2 = {EPUM_CONF_ENGINE: {CONF_PRESERVE_N:2}}
        yield self.epum.msg_reconfigure_epu(None, epu_name1, epu_config2)

        # should not take effect immediately, a reconfigure is external msg handled by reactor worker
        self.assertEqual(epu_engine1.reconfigure_count, 0)
        self.assertEqual(epu_engine2.reconfigure_count, 0)

        yield self.epum._run_decisions()

        # now it should have happened, after a decision cycle, but only to epu_name1
        self.assertEqual(epu_engine1.reconfigure_count, 1)
        self.assertEqual(epu_engine2.reconfigure_count, 0)

    @defer.inlineCallbacks
    def test_basic_npreserving(self):
        """
        Create one EPU with NPreserving=2 policy.
        Verify two instances are launched on the first decision cycle.
        """
        yield self.epum.initialize()
        epu_config = self._config_simplest_epuconf(2)
        yield self.epum.msg_add_epu(None, "testing123", epu_config)
        yield self.epum._run_decisions()
        self.assertEqual(self.provisioner_client.provision_count, 2)

    @defer.inlineCallbacks
    def test_reconfigure_npreserving(self):
        """
        Create one EPU with NPreserving=2 policy.
        Verify two instances are launched on the first decision cycle.
        Reconfigure with NPreserving=4 policy.
        Verify two more instances are launched on next decision cycle.
        Reconfigure with NPreserving=0 policy.
        Verify four instances are terminated on next decision cycle.
        """
        yield self.epum.initialize()
        epu_name = "testing123"
        epu_config = self._config_simplest_epuconf(2)
        
        yield self.epum.msg_add_epu(None, epu_name, epu_config)
        yield self.epum._run_decisions()
        self.assertEqual(self.provisioner_client.provision_count, 2)
        self.assertEqual(self.provisioner_client.terminate_node_count, 0)

        epu_config = self._config_simplest_epuconf(4)
        yield self.epum.msg_reconfigure_epu(None, epu_name, epu_config)
        yield self.epum._run_decisions()
        self.assertEqual(self.provisioner_client.provision_count, 4)
        self.assertEqual(self.provisioner_client.terminate_node_count, 0)

        epu_config = self._config_simplest_epuconf(0)
        yield self.epum.msg_reconfigure_epu(None, epu_name, epu_config)
        yield self.epum._run_decisions()
        self.assertEqual(self.provisioner_client.provision_count, 4)
        self.assertEqual(self.provisioner_client.terminate_node_count, 4)

    @defer.inlineCallbacks
    def test_decider_leader_disable(self):
        """
        Create one EPU with NPreserving=2 policy.
        Verify two instances are launched on the first decision cycle.
        Change to NPreserving=1, verify that one is terminated on second decision cycle
        Disable leader via epum internals
        Change to NPreserving=4, verify that nothing happened.
        Enable leader via epum internals
        Previous reconfiguration will be recognized

        This will only work in this in-memory situation, otherwise another EPUM worker becomes
        the decider and will respond to reconfigurations.
        """
        yield self.epum.initialize()
        epu_name = "testing123"
        epu_config = self._config_simplest_epuconf(2)

        yield self.epum.msg_add_epu(None, epu_name, epu_config)
        yield self.epum._run_decisions()
        self.assertEqual(self.provisioner_client.provision_count, 2)
        self.assertEqual(self.provisioner_client.terminate_node_count, 0)

        epu_config = self._config_simplest_epuconf(1)
        yield self.epum.msg_reconfigure_epu(None, epu_name, epu_config)
        yield self.epum._run_decisions()
        self.assertEqual(self.provisioner_client.provision_count, 2)
        self.assertEqual(self.provisioner_client.terminate_node_count, 1)

        # digging into internal structure to disable leader
        yield self.epum.epum_store._change_decider(False)

        # nothing should happen now, should stay provision=2, terminate=1
        epu_config = self._config_simplest_epuconf(4)
        yield self.epum.msg_reconfigure_epu(None, epu_name, epu_config)
        yield self.epum._run_decisions()
        self.assertEqual(self.provisioner_client.provision_count, 2)
        self.assertEqual(self.provisioner_client.terminate_node_count, 1)

        # digging into internal structure to enable leader
        yield self.epum.epum_store._change_decider(True)

        # previous reconfiguration (preserve 4) should be recognized if decision cycle runs
        yield self.epum._run_decisions()

        # 3 more provisions to take from N=1 to N=4 (making 5 total provisions)
        self.assertEqual(self.provisioner_client.provision_count, 5)
        self.assertEqual(self.provisioner_client.terminate_node_count, 1)

    @defer.inlineCallbacks
    def test_instance_lookup(self):
        """
        Create two EPUs, run NPreserving=1 in each of them.  Lookup by instance_id and make sure
        the right EPU is returned to the caller.  Some incoming service messages, like heartbeats,
        only have the  instance_id to go on (not which EPU it belongs to).
        """
        yield self.epum.initialize()
        epu_config = self._config_simplest_epuconf(1)
        epu_name1 = "epu1"
        epu_name2 = "epu2"
        yield self.epum.msg_add_epu(None, epu_name1, epu_config)
        yield self.epum._run_decisions()
        self.assertEqual(self.provisioner_client.provision_count, 1)
        self.assertEqual(len(self.provisioner_client.launched_instance_ids), 1)
        via_epu1 = self.provisioner_client.launched_instance_ids[0]

        yield self.epum.msg_add_epu(None, epu_name2, epu_config)
        yield self.epum._run_decisions()
        self.assertEqual(self.provisioner_client.provision_count, 2)
        self.assertEqual(len(self.provisioner_client.launched_instance_ids), 2)
        via_epu2 = self.provisioner_client.launched_instance_ids[1]

        epu1 = yield self.epum.epum_store.get_epu_state_by_instance_id(via_epu1)
        epu2 = yield self.epum.epum_store.get_epu_state_by_instance_id(via_epu2)

        self.assertEqual(epu1.epu_name, epu_name1)
        self.assertEqual(epu2.epu_name, epu_name2)

    @defer.inlineCallbacks
    def test_failing_engine_decide(self):
        """Exceptions during decide cycle should not affect EPUM.
        """
        yield self.epum.initialize()
        fail_config = self._config_mock2()
        yield self.epum.msg_add_epu(None, "fail_epu", fail_config)
        yield self.epum._run_decisions()
        # digging into internal structure to get engine instance
        epu_engine = yield self.epum.decider.engines["fail_epu"]
        self.assertEqual(epu_engine.decide_count, 1)

    @defer.inlineCallbacks
    def test_failing_engine_reconfigure(self):
        """Exceptions during engine reconfigure should not affect EPUM.
        """
        yield self.epum.initialize()
        fail_config = self._config_mock2()
        yield self.epum.msg_add_epu(None, "fail_epu", fail_config)
        yield self.epum._run_decisions()

        # digging into internal structure to get engine instance
        epu_engine = yield self.epum.decider.engines["fail_epu"]
        self.assertEqual(epu_engine.decide_count, 1)
        self.assertEqual(epu_engine.reconfigure_count, 0)

        config2 = {EPUM_CONF_ENGINE: {CONF_PRESERVE_N:2}}
        yield self.epum.msg_reconfigure_epu(None, "fail_epu", config2)
        yield self.epum._run_decisions()
        self.assertEqual(epu_engine.decide_count, 2)
        self.assertEqual(epu_engine.reconfigure_count, 1)

    @defer.inlineCallbacks
    def test_deferred_engine(self):
        """Engines should be able to use Deferred
        """
        yield self.epum.initialize()
        deferred_config = self._config_mock2()
        yield self.epum.msg_add_epu(None, "deferred_epu", deferred_config)
        yield self.epum._run_decisions()
        
        # digging into internal structure to get engine instance
        epu_engine = yield self.epum.decider.engines["deferred_epu"]
        self.assertEqual(epu_engine.decide_count, 1)
        yield self.epum._run_decisions()
        self.assertEqual(epu_engine.decide_count, 2)
        self.assertEqual(epu_engine.reconfigure_count, 0)

        config2 = {EPUM_CONF_ENGINE: {CONF_PRESERVE_N:2}}
        yield self.epum.msg_reconfigure_epu(None, "deferred_epu", config2)
        yield self.epum._run_decisions()
        self.assertEqual(epu_engine.reconfigure_count, 1)

# TODO
#    @defer.inlineCallbacks
#    def test_initialize_no_instance_recovery(self):
#
#    @defer.inlineCallbacks
#    def test_initialize_with_instance_recovery(self):

# TODO, perhaps
#    @defer.inlineCallbacks
#    def test_whole_state(self):
