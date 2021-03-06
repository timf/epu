class IEpuManagementClient(object):
    """This is a fake interface class that contains the methods any EPU Management client
    implementation will have.  There can be several types of messaging implementations
    that will have different implementations of this client.  And there can be mocks.

    This has the same method signatures as EPUManagement -- this fact is exploited
    in test/dev situations to bypass the messaging layer altogether.

    See EPUManagement for parameter documentation.
    """
    def msg_register_need(self, caller, dt_id, constraints, num_needed, subscriber_name, subscriber_op):
        pass

    def msg_retire_node(self, caller, node_id):
        pass

    def msg_subscribe_dt(self, caller, dt_id, subscriber_name, subscriber_op):
        pass

    def msg_unsubscribe_dt(self, caller, dt_id, subscriber_name):
        pass

    def msg_add_epu(self, caller, dt_id, epu_config):
        pass

    def msg_remove_epu(self, caller, dt_id):
        pass

    def msg_reconfigure_epu(self, caller,  dt_id, epu_config):
        pass

    def msg_heartbeat(self, caller, content):
        pass

    def msg_instance_info(self, caller, content):
        pass

    def msg_sensor_info(self, caller, content):
        pass


class IProvisionerClient(object):
    """This is a fake interface class that contains the methods any provisioner client
    implementation will have.  There can be several types of messaging implementations
    that will have different implementations of this client.  And there can be mocks.
    """
    def provision(self, launch_id, deployable_type, launch_description, subscribers, vars=None):
        pass

    def terminate_launches(self, launches):
        pass

    def terminate_nodes(self, nodes):
        pass

    def terminate_all(self, rpcwait=False, retries=5, poll=1.0):
        pass

    def dump_state(self, nodes, force_subscribe=None):
        pass

class ISubscriberNotifier(object):
    """This is a fake interface class that contains the methods any subscriber notifier
    implementation will have.  There can be several types of messaging implementations
    that will have different implementations of this client.  And there can be mocks.
    """
    def notify_by_name(self, receiver_name, operation, message):
        """The name is translated into the appropriate messaging-layer object.
        @param receiver_name Message layer name
        @param operation The operation to call on that name
        @param message dict to send
        """
        pass

class IOUAgentClient(object):
    """This is a fake interface class that contains the methods any OU Agent implementation
    will have.  There can be several types of messaging implementations that will have
    different implementations of this client.  And there can be mocks.

    TODO: This belongs in epuagent repository
    """
    def dump_state(self, target_address):
        """Send a heartbeat ASAP.
        """
        pass

    def get_error_info(self, pid, receiver_name):
        """Request the error output from a process, given the ID.
        TODO: Currently this is unused, the heartbeat sends error each message for simplicity.
        """
        pass
