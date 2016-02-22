from __future__ import unicode_literals

import random
import binascii
from past.builtins import long

from twisted.internet import defer

from storjrpcudp.protocol import RPCProtocol

from storjkademlia.node import Node
from storjkademlia.routing import RoutingTable
from storjkademlia.log import Logger
from storjkademlia.utils import digest


class KademliaProtocol(RPCProtocol):
    def __init__(self, sourceNode, storage, ksize=20, logger=None):
        RPCProtocol.__init__(self)
        self.router = RoutingTable(self, ksize, sourceNode)
        self.storage = storage
        self.sourceNode = sourceNode
        self.log = logger or Logger(system=self)

    def getRefreshIDs(self):
        """
        Get ids to search for to keep old buckets up to date.
        """
        ids = []
        for bucket in self.router.getLonelyBuckets():
            ids.append(random.randint(*bucket.range))
        return ids

    def rpc_stun(self, sender):
        return sender

    def rpc_ping(self, sender, nodeid):
        source = Node(nodeid, sender[0], sender[1])
        self.welcomeIfNewNode(source)
        return self.sourceNode.id

    def rpc_store(self, sender, nodeid, key, value):
        source = Node(nodeid, sender[0], sender[1])
        self.welcomeIfNewNode(source)
        self.log.debug("got a store request from %s, storing value" % str(sender))
        self.storage[key] = value
        return True

    def rpc_find_node(self, sender, nodeid, key):
        self.log.info("finding neighbors of %i in local table" % long(binascii.hexlify(nodeid), 16))
        source = Node(nodeid, sender[0], sender[1])
        self.welcomeIfNewNode(source)
        node = Node(key)
        return list(map(tuple, self.router.findNeighbors(node, exclude=source)))

    def rpc_find_value(self, sender, nodeid, key):
        source = Node(nodeid, sender[0], sender[1])
        self.welcomeIfNewNode(source)
        value = self.storage.get(key, None)
        if value is None:
            return self.rpc_find_node(sender, nodeid, key)
        return { 'value': value }

    def callFindNode(self, nodeToAsk, nodeToFind):
        address = (nodeToAsk.ip, nodeToAsk.port)
        d = self.find_node(address, self.sourceNode.id, nodeToFind.id)
        d.addCallback(self.handleCallResponse, nodeToAsk)
        d.addErrback(self.onError)
        return d

    def callFindValue(self, nodeToAsk, nodeToFind):
        address = (nodeToAsk.ip, nodeToAsk.port)
        d = self.find_value(address, self.sourceNode.id, nodeToFind.id)
        d.addCallback(self.handleCallResponse, nodeToAsk)
        d.addErrback(self.onError)
        return d

    def callPing(self, nodeToAsk):
        address = (nodeToAsk.ip, nodeToAsk.port)
        d = self.ping(address, self.sourceNode.id)
        d.addCallback(self.handleCallResponse, nodeToAsk)
        d.addErrback(self.onError)
        return d

    def callStore(self, nodeToAsk, key, value):
        address = (nodeToAsk.ip, nodeToAsk.port)
        d = self.store(address, self.sourceNode.id, key, value)
        d.addCallback(self.handleCallResponse, nodeToAsk)
        d.addErrback(self.onError)
        return d

    def onError(self, err):
        self.log.error(repr(err))
        return err

    def welcomeIfNewNode(self, node):
        """
        Given a new node, send it all the keys/values it should be storing,
        then add it to the routing table.

        @param node: A new node that just joined (or that we just found out
        about).

        Process:
        For each key in storage, get k closest nodes.  If newnode is closer
        than the furtherst in that list, and the node for this server
        is closer than the closest in that list, then store the key/value
        on the new node (per section 2.5 of the paper)
        """
        if self.router.isNewNode(node):
            ds = []
            for key, value in self.storage.iteritems():
                keynode = Node(digest(key))
                neighbors = self.router.findNeighbors(keynode)
                if len(neighbors) > 0:
                    newNodeClose = node.distanceTo(keynode) < neighbors[-1].distanceTo(keynode)
                    thisNodeClosest = self.sourceNode.distanceTo(keynode) < neighbors[0].distanceTo(keynode)
                if len(neighbors) == 0 or (newNodeClose and thisNodeClosest):
                    ds.append(self.callStore(node, key, value))
            self.router.addContact(node)
            return defer.gatherResults(ds)

    def handleCallResponse(self, result, node):
        """
        If we get a response, add the node to the routing table.  If
        we get no response, make sure it's removed from the routing table.
        """
        if result[0]:
            self.log.info("got response from %s, adding to router" % node)
            self.welcomeIfNewNode(node)
        else:
            self.log.debug("no response from %s, removing from router" % node)
            self.router.removeContact(node)
        return result
