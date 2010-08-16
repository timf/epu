#!/usr/bin/env python

"""
@file ion/services/sa/data_acquisition.py
@author Michael Meisinger
@brief service for data acquisition
"""

import logging
logging = logging.getLogger(__name__)
from twisted.internet import defer

from ion.agents.instrumentagents.instrument_agent import InstrumentAgentClient
from ion.core.base_process import ProtocolFactory
from ion.data.dataobject import DataObject, ResourceReference, LCStates
from ion.resources.coi_resource_descriptions import AgentInstance
from ion.resources.sa_resource_descriptions import InstrumentResource, DataProductResource
from ion.resources.ipaa_resource_descriptions import InstrumentAgentResourceInstance
from ion.services.base_service import BaseService, BaseServiceClient
from ion.services.sa.instrument_registry import InstrumentRegistryClient
from ion.services.sa.data_product_registry import DataProductRegistryClient
from ion.services.coi.agent_registry import AgentRegistryClient
import ion.util.procutils as pu

class InstrumentManagementService(BaseService):
    """
    Instrument management service interface.
    This service provides overall coordination for instrument management within
    an observatory context. In particular it coordinates the access to the
    instrument and data product registries and the interaction with instrument
    agents.
    """

    # Declaration of service
    declare = BaseService.service_declare(name='instrument_management',
                                          version='0.1.0',
                                          dependencies=[])

    def slc_init(self):
        self.irc = InstrumentRegistryClient(proc=self)
        self.dprc = DataProductRegistryClient(proc=self)
        self.arc = AgentRegistryClient(proc=self)

    @defer.inlineCallbacks
    def op_create_new_instrument(self, content, headers, msg):
        """
        Service operation: Accepts a dictionary containing user inputs and updates the instrument
        registry.
        """
        userInput = content['userInput']

        newinstrument = InstrumentResource.create_new_resource()

        if 'name' in userInput:
            newinstrument.name = str(userInput['name'])

        if 'manufacturer' in userInput:
            newinstrument.manufacturer = str(userInput['manufacturer'])

        if 'model' in userInput:
            newinstrument.model = str(userInput['model'])

        if 'serial_num' in userInput:
            newinstrument.serial_num = str(userInput['serial_num'])

        if 'fw_version' in userInput:
            newinstrument.fw_version = str(userInput['fw_version'])

        instrument_res = yield self.irc.register_instrument_instance(newinstrument)

        yield self.reply_ok(msg, instrument_res.encode())

    @defer.inlineCallbacks
    def op_create_new_data_product(self, content, headers, msg):
        """
        Service operation: Accepts a dictionary containing user inputs and
        updates the data product registry.
        """
        dataProductInput = content['dataProductInput']

        newdp = DataProductResource.create_new_resource()
        if 'instrumentID' in dataProductInput:
            inst_id = str(dataProductInput['instrumentID'])
            int_ref = ResourceReference(RegistryIdentity=inst_id, RegistryBranch='master')
            newdp.instrument_ref = int_ref

        if 'dataformat' in dataProductInput:
            newdp.dataformat = str(dataProductInput['dataformat'])

        # Step: Create a data stream

        res = yield self.dprc.register_data_product(newdp)
        ref = res.reference(head=True)

        yield self.reply_ok(msg, res.encode())

    @defer.inlineCallbacks
    def op_execute_command(self, content, headers, msg):
        """
        Service operation: Execute a command on an instrument.
        """

        # Step 1: Extract the arguments from the UI generated message content
        commandInput = content['commandInput']

        if 'instrumentID' in commandInput:
            inst_id = str(commandInput['instrumentID'])
        else:
            raise ValueError("Input for instrumentID not present")

        command = []
        if 'command' in commandInput:
            command_op = str(commandInput['command'])
        else:
            raise ValueError("Input for command not present")

        command.append(command_op)

        arg_idx = 0
        while True:
            argname = 'cmdArg'+str(arg_idx)
            arg_idx += 1
            if argname in commandInput:
                command.append(str(commandInput[argname]))
            else:
                break

        # Step 2: Find the agent id for the given instrument id
        agent_pid  = yield self.get_agent_pid_for_instrument(inst_id)
        if not agent_pid:
            yield self.reply_err(msg, "No agent found for instrument "+str(inst_id))
            defer.returnValue(None)

        # Step 3: Interact with the agent to execute the command
        iaclient = InstrumentAgentClient(proc=self, target=agent_pid)
        commandlist = [command,]
        logging.info("Sending command to IA: "+str(commandlist))
        cmd_result = yield iaclient.execute_instrument(commandlist)

        yield self.reply_ok(msg, cmd_result)

    @defer.inlineCallbacks
    def op_get_instrument_state(self, content, headers, msg):
        """
        Service operation: .
        """
        # Step 1: Extract the arguments from the UI generated message content
        commandInput = content['commandInput']

        if 'instrumentID' in commandInput:
            inst_id = str(commandInput['instrumentID'])
        else:
            yield reply_error(msg, "Input for instrumentID not present")
            return

        agent_pid = yield self.get_agent_pid_for_instrument(inst_id)
        if not agent_pid:
            yield self.reply_err(msg, "No agent found for instrument "+str(inst_id))
            defer.returnValue(None)

        iaclient = InstrumentAgentClient(proc=self, target=agent_pid)
        inst_cap = yield iaclient.get_capabilities()
        if not inst_cap:
            yield self.reply_err(msg, "No capabilities available for instrument "+str(inst_id))
            defer.returnValue(None)

        ci_commands = inst_cap['ci_commands']
        instrument_commands = inst_cap['instrument_commands']
        instrument_parameters = inst_cap['instrument_parameters']
        ci_parameters = inst_cap['ci_parameters']

        values = yield iaclient.get_from_instrument(instrument_parameters)
        resvalues = {}
        if values:
            resvalues = values

        yield self.reply_ok(msg, resvalues)


    @defer.inlineCallbacks
    def op_start_direct_access(self, content, headers, msg):
        """
        Service operation: Starts direct access mode.
        """
        yield self.reply_err(msg, "Not yet implemented")

    @defer.inlineCallbacks
    def op_stop_direct_access(self, content, headers, msg):
        """
        Service operation: Stops direct access mode.
        """
        yield self.reply_err(msg, "Not yet implemented")

    @defer.inlineCallbacks
    def get_agent_for_instrument(self, instrument_id):
        logging.info("get_agent_for_instrument() instrumentID="+str(instrument_id))
        int_ref = ResourceReference(RegistryIdentity=instrument_id, RegistryBranch='master')
        agent_query = InstrumentAgentResourceInstance()
        agent_query.instrument_ref = int_ref
        # @todo Need to list the LC state here. WHY???
        agent_query.lifecycle = LCStates.developed
        agents = yield self.arc.find_registered_agent_instance_from_description(agent_query, regex=False)
        logging.info("Found %s agent instances for instrument id %s" % (len(agents), instrument_id))
        agent_res = None
        if len(agents) > 0:
            agent_res = agents[0]
        defer.returnValue(agent_res)

    @defer.inlineCallbacks
    def get_agent_pid_for_instrument(self, instrument_id):
        agent_res = yield self.get_agent_for_instrument(instrument_id)
        if not agent_res:
            defer.returnValue(None)
        agent_pid = agent_res.proc_id
        logging.info("Agent process id for instrument id %s is: %s" % (instrument_id, agent_pid))
        defer.returnValue(agent_pid)

class InstrumentManagementClient(BaseServiceClient):
    """
    Class for the client accessing the instrument management service.
    """
    def __init__(self, proc=None, **kwargs):
        if not 'targetname' in kwargs:
            kwargs['targetname'] = "instrument_management"
        BaseServiceClient.__init__(self, proc, **kwargs)

    @defer.inlineCallbacks
    def create_new_instrument(self, userInput):
        reqcont = {}
        reqcont['userInput'] = userInput

        (content, headers, message) = yield self.rpc_send('create_new_instrument',
                                                          reqcont)
        defer.returnValue(DataObject.decode(content['value']))

    @defer.inlineCallbacks
    def create_new_data_product(self, dataProductInput):
        reqcont = {}
        reqcont['dataProductInput'] = dataProductInput

        (content, headers, message) = yield self.rpc_send('create_new_data_product',
                                                          reqcont)
        defer.returnValue(DataObject.decode(content['value']))

    @defer.inlineCallbacks
    def get_instrument_state(self, instrumentID):
        reqcont = {}
        commandInput = {}
        commandInput['instrumentID'] = instrumentID
        reqcont['commandInput'] = commandInput

        (cont, hdrs, msg) = yield self.rpc_send('get_instrument_state', reqcont)
        if cont.get('status') == 'OK':
            defer.returnValue(cont)
        else:
            defer.returnValue(None)

    @defer.inlineCallbacks
    def execute_command(self, instrumentID, command, arglist):
        reqcont = {}
        commandInput = {}
        commandInput['instrumentID'] = instrumentID
        commandInput['command'] = command
        if arglist:
            argnum = 0
            for arg in arglist:
                commandInput['cmdArg'+str(argnum)] = arg
                argnum += 1
        reqcont['commandInput'] = commandInput

        (cont, hdrs, msg) = yield self.rpc_send('execute_command', reqcont)
        if cont.get('status') == 'OK':
            defer.returnValue(cont)
        else:
            defer.returnValue(None)

# Spawn of the process using the module name
factory = ProtocolFactory(InstrumentManagementService)
