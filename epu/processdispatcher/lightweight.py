from itertools import ifilter
from twisted.internet import defer

import ion.util.ionlog
import epu.states as InstanceStates
from epu.processdispatcher.states import ProcessStates

log = ion.util.ionlog.getLogger(__name__)


class ProcessState(object):
    """A single process request in the system

    """
    def __init__(self, epid, spec, state, subscribers, constraints=None,
                 round=0, priority=0, immediate=False):
        self.epid = epid
        self.spec = spec
        self.state = state
        self.subscribers = subscribers
        self.constraints = constraints
        self.round = round
        self.priority = priority
        self.immediate = immediate

        self.assigned = None

    def check_resource_match(self, resource):
        return match_constraints(self.constraints, resource.properties)


class ExecutionEngineRegistryEntry(object):
    def __init__(self, deployable_type, execution_engines):
        self.deployable_type = deployable_type
        self.execution_engines = list(execution_engines)


class ExecutionEngineRegistry(object):
    """Simple in-memory registry mapping deployable types to execution engines
    """
    def __init__(self):
        self.by_ee = {}
        self.by_dt = {}

    def add_entry(self, entry):
        self.by_dt[entry.deployable_type] = entry
        for ee in entry.execution_engines:
            self.by_ee[ee] = entry

    def get_by_engine_type(self, engine_type):
        return self.by_ee.get(engine_type)

    def get_by_deployable_type(self, deployable_type):
        return self.by_dt.get(deployable_type)


class DeployedNode(object):
    def __init__(self, node_id, dt, properties=None):
        self.node_id = node_id
        self.dt = dt
        self.properties = properties

        self.resources = []


class ExecutionEngineResource(object):
    """A single EE resource
    """
    def __init__(self, node_id, ee_id, properties=None):
        self.node_id = node_id
        self.ee_id = ee_id
        self.properties = properties

        self.last_heartbeat = None
        self.slot_count = 0
        self.processes = {}
        self.pending = set()

        self.enabled = True

    @property
    def available_slots(self):
        if not self.enabled:
            return 0

        return max(0, self.slot_count - len(self.pending))

    def disable(self):
        self.enabled = False

    def enable(self):
        self.enabled = True

    def add_pending_process(self, process):
        """Mark a process as pending deployment to this resource
        """
        epid = process.epid
        assert epid in self.pending or self.slot_count > 0, "no slot available"
        assert process.assigned == self.ee_id
        self.pending.add(epid)

    def check_process_match(self, process):
        """Check if this resource is valid for a process' constraints
        """
        return match_constraints(process.constraints, self.properties)


class ProcessDispatcherCore(object):
    """Service that fields requests from application engines and operators
    for process launches and termination.

    The PD has several responsibilities:

        - Receive and process requests from clients. These requests dictate
          which processes should be running. There may also be information
          queries about the state of the system.

        - Track available execution engine resources. It subscribes to a feed
          of DT deployment information from EPUM and uses this along with
          direct EEAgent heartbeats to determine available and healthy
          resources.

        - Maintain a priority queue of runnable WAITING processes. Matchmake
          processes with available resources and send dispatch requests to
          EEAgents. When resources are not available, escalate to EPUM for
          more DTs of a compatible type.

        - Track state of all processes in the system. When a process dies or
          is killed, attempt to replace it (and perhaps give it a higher
          launch priority than other WAITING processes). If a process
          repeatedly fails on its own (not due to VMs dying wholesale), mark
          it as FAILED and report to client.

    """

    def __init__(self, ee_registry, eeagent_client, notifier):
        self.ee_registry = ee_registry
        self.eeagent_client = eeagent_client
        self.notifier = notifier

        self.processes = {}
        self.resources = {}
        self.nodes = {}

        self.queue = []


    @defer.inlineCallbacks
    def dispatch_process(self, epid, spec, subscribers, constraints=None, immediate=False):
        """Dispatch a new process into the system

        @param epid: unique process identifier
        @param spec: description of what is started
        @param subscribers: where to send status updates of this process
        @param constraints: optional scheduling constraints (IaaS site? other stuff?)
        @param immediate: don't provision new resources if no slots are available
        @rtype: L{ProcessState}
        @return: description of process launch status


        This is an RPC-style call that returns quickly, as soon as a decision is made:

            1. If a matching slot is available, dispatch begins and a PENDING
               response is sent. Further updates are sent to subscribers.

            2. If no matching slot is available, behavior depends on immediate flag
               - If immediate is True, an error is returned
               - If immediate is False, a provision request is sent and
                 WAITING is returned. Further updates are sent to subscribers.

        At the point of return, the request is either pending (and guaranteed
        to be followed through til error or success), or has failed.


        Retry
        =====
        If a call to this operation times out without a reply, it can safely
        be retried. The epid and other parameters will be used to ensure that
        nothing is repeated. If the service fields an operation request that
        it thinks has already been acknowledged, it will return the current
        state of the process (or a defined AlreadyDidThatError if that is too
        difficult).
        """
        try:
            if epid in self.processes:
                defer.returnValue(self.processes[epid])

            process = ProcessState(epid, spec, ProcessStates.REQUESTED,
                                   subscribers, constraints, immediate=immediate)

            self.processes[epid] = process

            yield self._matchmake_process(process)
            defer.returnValue(process)
        except Exception:
            log.exception("faillll")
            raise

    def _matchmake_process(self, process):
        """Match process against available resources and dispatch if matched

        @param process:
        @return:
        """

        # do an inefficient search, shrug
        not_full = ifilter(lambda r: r.available_slots > 0,
                           self.resources.itervalues())
        matching = filter(process.check_resource_match, not_full)

        if not matching:

            if process.immediate:
                log.info("Process %s: no available slots. "+
                         "REJECTED due to immediate flag", process.epid)
                process.state = ProcessStates.REJECTED

            else:
                log.info("Process %s: no available slots. WAITING in queue",
                     process.epid)

                process.state = ProcessStates.WAITING
                self.queue.append(process)

            return defer.succeed(None)

        else:
            # pick a resource with the lowest available slot count, cheating
            # way to try and enforce compaction for now.
            resource = min(matching, key=lambda r: r.slot_count)

            return self._dispatch_matched_process(process, resource)

    def _dispatch_matched_process(self, process, resource):
        """Enact a match between process and resource
        """
        ee = resource.ee_id

        log.info("Process %s assigned slot on %s. PENDING!", process.epid, ee)

        process.assigned = ee
        process.state = ProcessStates.PENDING

        resource.add_pending_process(process)

        return self.eeagent_client.dispatch_process(ee, process.epid,
                                                    process.round,
                                                    process.spec)

    @defer.inlineCallbacks
    def terminate_process(self, epid):
        """
        Kill a running process
        @param epid: ID of process
        @rtype: L{ProcessState}
        @return: description of process termination status

        This is an RPC-style call that returns quickly, as soon as termination
        of the process has begun (TERMINATING state).

        Retry
        =====
        If a call to this operation times out without a reply, it can safely
        be retried. Termination of processes should be an idempotent operation
        here and at the EEAgent. It is important that eeids not be repeated to
        faciliate this.
        """

        #TODO process might not exist
        process = self.processes[epid]

        if process.state >= ProcessStates.TERMINATED:
            defer.returnValue(process)

        if process.assigned is None:
            process.state = ProcessStates.TERMINATED
            defer.returnValue(process)

        yield self.eeagent_client.terminate_process(process.assigned, epid)

        process.state = ProcessStates.TERMINATING
        defer.returnValue(process)

    @defer.inlineCallbacks
    def dt_state(self, node_id, deployable_type, state, properties=None):
        """
        Handle updates about available instances of deployable types.

        @param node_id: unique instance identifier
        @param deployable_type: type of instance
        @param state: EPU state of instance
        @param properties: Optional properties about this instance
        @return:

        This operation is the recipient of a "subscription" the PD makes to
        DT state updates. Calls to this operation are NOT RPC-style.

        This information is used for two purposes:

            1. To correlate EE agent heartbeats with a DT and various deploy
               information (site, allocation, security groups, etc).

            2. To detect EEs which have been killed due to underlying death
               of a resource (VM).
        """

        if state == InstanceStates.RUNNING:
            if node_id not in self.nodes:
                node = DeployedNode(node_id, deployable_type, properties)
                self.nodes[node_id] = node

        elif state in (InstanceStates.TERMINATING, InstanceStates.TERMINATED):
            # reschedule processes running on node

            node = self.nodes.get(node_id)
            if node is None:
                log.warn("Got dt_state for unknown node %s in state %s",
                         node_id, state)
                defer.returnValue(None)

            # first walk resources and mark ineligible for scheduling
            for resource in node.resources:
                resource.disable()

            # go through resources on this node and reschedule any processes
            for resource in node.resources:
                for epid in resource.processes:

                    process = self.processes.get(epid)
                    if process is None:
                        continue

                    # send a last ditch terminate just in case
                    if process.state < ProcessStates.TERMINATED:
                        yield self.eeagent_client.terminate_process(
                            resource.ee_id, epid)

                    if process.state == ProcessStates.TERMINATING:

                        #what luck
                        process.state = ProcessStates.TERMINATED
                        yield self.notifier.notify_process(process)

                    elif process.state < ProcessStates.TERMINATING:

                        process.round += 1
                        process.assigned = None
                        process.state = ProcessStates.DIED_REQUESTED
                        yield self.notifier.notify_process(process)
                        yield self._matchmake_process(process)
                        yield self.notifier.notify_process(process)

            del self.nodes[node_id]
            for resource in node.resources:
                del self.resources[resource.ee_id]

    @defer.inlineCallbacks
    def ee_heartbeart(self, sender, beat):
        """Incoming heartbeat from an EEAgent

        @param sender: ION name of sender
        @param beat: information about running processes
        @return:

        When an EEAgent starts, it immediately begins sending heartbeats to
        the PD. The first received heartbeat will trigger the PD to mark the
        EE as available in its slot tables, and potentially start deploying
        some WAITING process requests.

        The heartbeat message will consist of at least these fields:
            - node id - unique ID for the provisioned resource (VM) the EE runs on
            - timestamp - time heartbeat was generated
            - processes - list of running process IDs
            - slot_count - number of available slots
        """

        node_id = beat['node_id']
        engine_type = beat['engine_type']
        processes = beat['processes']
        slot_count = int(beat['slot_count'])

        resource = self.resources.get(sender)
        if resource is None:
            # first heartbeat from this EE

            node = self.nodes.get(node_id)
            if node is None:
                log.warn("EE heartbeat from unknown node. Still booting? "+
                         "node_id=%s sender=%s", node_id, sender)

                # TODO I'm thinking the best thing to do here is query EPUM
                # for the state of this node in case the initial dt_state
                # update got lost. Note that we shouldn't go ahead and
                # schedule processes onto this EE until we get the RUNNING
                # dt_state update -- there could be a failure later on in
                # the contextualization process that triggers the node to be
                # terminated.

                defer.returnValue(None)

            if node.properties:
                properties = node.properties.copy()
            else:
                properties = {}

            # just making engine type a generic property/constraint for now,
            # until it is clear something more formal is needed.
            properties['engine_type'] = engine_type

            resource = ExecutionEngineResource(node_id, sender, properties)
            self.resources[sender] = resource
            node.resources.append(resource)

            log.info("Got first heartbeat from EEAgent %s on node %s",
                     sender, node_id)

        running_epids = []
        for epid, round, state in processes:

            if state <= ProcessStates.RUNNING:
                running_epids.append(epid)

            process = self.processes.get(epid)
            if not process:
                log.warn("EE reports process %s that is unknown!", epid)
                continue

            if round < process.round:
                # skip heartbeat info for processes that are already redeploying
                continue

            if epid in resource.pending:
                resource.pending.remove(epid)

            if state == process.state:
                continue

            if process.state == ProcessStates.PENDING and \
               state == ProcessStates.RUNNING:

                # mark as running and notify subscriber
                process.state = ProcessStates.RUNNING
                yield self.notifier.notify_process(process)

            elif state in (ProcessStates.TERMINATED, ProcessStates.FAILED):

                # process has died in resource. Obvious culprit is that it was
                # killed on request.

                if process.state == ProcessStates.TERMINATING:
                    # mark as terminated and notify subscriber
                    process.state = ProcessStates.TERMINATED
                    process.assigned = None
                    yield self.notifier.notify_process(process)

                # otherwise it needs to be rescheduled
                elif process.state in (ProcessStates.PENDING,
                                    ProcessStates.RUNNING):

                    process.state = ProcessStates.DIED_REQUESTED
                    process.assigned = None
                    process.round += 1
                    yield self.notifier.notify_process(process)
                    yield self._matchmake_process(process)

                # send cleanup request to EEAgent now that we have dealt
                # with the dead process
                yield self.eeagent_client.cleanup_process(sender, epid)

        resource.processes = running_epids
        
        new_slots_available = slot_count > resource.slot_count
        resource.slot_count = slot_count

        if new_slots_available:
            yield self._consider_resource(resource)

    def dump(self):
        resources = {}
        processes = {}
        state = dict(resources=resources, processes=processes)

        for resource in self.resources.itervalues():
            resource_dict = dict(ee_id=resource.ee_id,
                                 node_id=resource.node_id,
                                 processes=resource.processes,
                                 slot_count=resource.slot_count)
            resources[resource.ee_id] = resource_dict

        for process in self.processes.itervalues():
            process_dict = dict(epid=process.epid, round=process.round,
                                state=process.state,
                                assigned=process.assigned)
            processes[process.epid] = process_dict

        return defer.succeed(state)

    @defer.inlineCallbacks
    def _consider_resource(self, resource):
        """Consider a resource that has had new slots become available

        Because we operate in a single-threaded mode in this lightweight
        prototype, we don't need to worry about other half-finished requests.

        @param resource: The resource with new slots
        @return: None
        """
        matched = set()
        for process in ifilter(resource.check_process_match, self.queue):

            if not resource.available_slots:
                break

            matched.add(process.epid)
            yield self._dispatch_matched_process(process, resource)

        # dumb slow whatever.
        if matched:
            self.queue = [p for p in self.queue if p.epid not in matched]


def match_constraints(constraints, properties):
    """Match process constraints against resource properties

    Simple equality matches for now.
    """
    if constraints is None:
        return True

    for key,value in constraints.iteritems():
        if value is None:
            continue

        if properties is None:
            return False

        advertised = properties.get(key)
        if advertised is None:
            return False

        if isinstance(value,(list,tuple)):
            if not advertised in value:
                return False
        else:
            if advertised != value:
                return False

    return True

