# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
# pylint: disable=W0614,C0103,W0142
#
# Copyright (c) 2014, Arista Networks, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
#   Redistributions of source code must retain the above copyright notice,
#   this list of conditions and the following disclaimer.
#
#   Redistributions in binary form must reproduce the above copyright
#   notice, this list of conditions and the following disclaimer in the
#   documentation and/or other materials provided with the distribution.
#
#   Neither the name of Arista Networks nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL ARISTA NETWORKS
# BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR
# BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
# WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
# OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN
# IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#

import collections
import logging
import os
import re

import ztpserver.config
import ztpserver.serializers

from ztpserver.constants import CONTENT_TYPE_OTHER
from ztpserver.constants import CONTENT_TYPE_YAML

DEVICENAME_PARSER_RE = re.compile(r":(?=[Ethernet|\d+(?/)(?\d+)|\*])")
ANYDEVICE_PARSER_RE = re.compile(r":(?=[any])")
FUNC_RE = re.compile(r"(?P<function>\w+)(?=\(\S+\))\([\'|\"]"
                     r"(?P<arg>.+?)[\'|\"]\)")

log = logging.getLogger(__name__)
serializer = ztpserver.serializers.Serializer()

class Collection(collections.Mapping, collections.Callable):
    def __init__(self):
        self.data = dict()

    def __call__(self, key=None):
        #pylint: disable=W0221
        return self.keys() if key is None else self.get(key)

    def __getitem__(self, key):
        return self.data[key]

    def __iter__(self):
        return iter(self.data)

    def __len__(self):
        return len(self.data)

    def __delitem__(self, key):
        del self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value


class OrderedCollection(collections.OrderedDict, collections.Callable):
    def __call__(self, key=None):
        #pylint: disable=W0221
        return self.keys() if key is None else self.get(key)

class DataFileError(Exception):
    pass

class DataFileMixin(object):

    def load(self, fobj, content_type=CONTENT_TYPE_OTHER):
        raise NotImplementedError

    def loads(self, data, content_type=CONTENT_TYPE_OTHER):
        raise NotImplementedError

    def dump(self, fobj, data, content_type=CONTENT_TYPE_OTHER):
        raise NotImplementedError

    def dumps(self, data, content_type=CONTENT_TYPE_OTHER):
        raise NotImplementedError

    def serialize(self):
        raise NotImplementedError

    def deserialize(self):
        raise NotImplementedError

class NodeErrror(Exception):
    pass

class Node(object):

    Neighbor = collections.namedtuple("Neighbor", ['device', 'port'])

    def __init__(self, **kwargs):
        kwargs = ztpserver.serializers.Serializer.convert(kwargs)
        self.model = kwargs.get('model')
        self.systemmac = kwargs.get('systemmac')
        self.serialnumber = kwargs.get('serialnumber')
        self.version = kwargs.get('version')

        self.neighbors = OrderedCollection()
        if 'neighbors' in kwargs:
            self.add_neighbors(kwargs['neighbors'])

        super(Node, self).__init__()

    def __repr__(self):
        return "Node(neighbors=%d)" % len(self.neighbors)

    def add_neighbors(self, neighbors):
        for interface, neighbor_list in neighbors.items():
            collection = list()
            for neighbor in neighbor_list:
                collection.append(self.Neighbor(**neighbor))
            self.neighbors[interface] = collection

    def hasneighbors(self):
        return len(self.neighbors) > 0

    def serialize(self):
        attrs = dict()
        for prop in ['model', 'systemmac', 'serialnumber', 'version']:
            if getattr(self, prop) is not None:
                attrs[prop] = getattr(self, prop)

        neighbors = dict()
        if self.hasneighbors():
            for interface, neighbors in self.neighbors.items():
                collection = list()
                for neighbor in neighbors:
                    collection.append(dict(device=neighbor.device,
                                           port=neighbor.port))
                neighbors[interface] = collection
        attrs['neighbors'] = neighbors

class ResourcesError(Exception):
    pass

class Resources(object):

    def __init__(self):
        filepath = ztpserver.config.runtime.default.data_root
        self.workingdir = os.path.join(filepath, "resources")

    def load(self, fobj, content_type=CONTENT_TYPE_YAML):
        try:
            contents = self.loads(fobj.read(), content_type)
        except IOError as exc:
            log.debug(exc)
            raise DataFileError('unable to load file')
        return contents

    @classmethod
    def loads(cls, data, content_type=CONTENT_TYPE_YAML):
        try:
            contents = serializer.deserialize(data, content_type)
            return contents
        except ztpserver.serializers.SerializerError as exc:
            log.debug(exc)
            raise DataFileError('unable to load data')

    def dumps(self, data, pool, content_type=CONTENT_TYPE_YAML):
        fobj = open(pool)
        self.dump(data, fobj, content_type)

    @classmethod
    def dump(cls, data, fobj, content_type=CONTENT_TYPE_YAML):
        try:
            contents = serializer.serialize(data, content_type)
            fobj.write(contents)
        except IOError as exc:
            log.debug(exc)
            raise DataFileError("unable to write file")

    def allocate(self, pool, node):
        match = self.lookup(pool, node)
        if match:
            log.debug("Found allocated resources, returning %s" % match)
            return match

        file_path = os.path.join(self.workingdir, pool)
        contents = self.load(open(file_path))

        try:
            key = next(x[0] for x in contents.items() if x[1] is None)
            contents[key] = node.systemmac
        except StopIteration:
            raise ResourcesError('no resources available in pool')

        self.dump(contents, open(file_path, 'w'))
        return key

    def lookup(self, pool, node):
        log.debug("Looking up resource for node %s" % node.systemmac)
        file_path = os.path.join(self.workingdir, pool)
        contents = self.load(open(file_path))
        matches = [x[0] for x in contents.items() if x[1] == node.systemmac]
        key = matches[0] if matches else None
        return key


class Functions(object):

    @classmethod
    def exact(cls, arg, value):
        return arg == value

    @classmethod
    def regex(cls, arg, value):
        match = re.match(arg, value)
        return True if match else False

    @classmethod
    def includes(cls, arg, value):
        return arg in value

    @classmethod
    def excludes(cls, arg, value):
        return arg not in value


class TopologyError(Exception):
    pass

class Topology(object):

    def __init__(self, contents=None):
        self.variables = dict()
        self.patterns = {'globals': list(), 'nodes': dict()}

        if contents is not None:
            self.deserialize(contents)

    def __repr__(self):
        return "Topology(globals=%d, nodes=%d)" % \
            (len(self.patterns['globals']), len(self.patterns['nodes']))

    def load(self, fobj, content_type=CONTENT_TYPE_YAML):
        try:
            self.loads(fobj.read(), content_type)
        except IOError as exc:
            log.debug(exc)
            raise TopologyError('unable to load file')

    def loads(self, data, content_type=CONTENT_TYPE_YAML):
        try:
            contents = serializer.deserialize(data, content_type)
            self.deserialize(contents)
        except ztpserver.serializers.SerializerError as exc:
            log.debug(exc)
            raise TopologyError('unable to load data')

    def deserialize(self, contents):
        self.variables = contents.get('variables') or dict()
        if 'any' in self.variables or 'none' in self.variables:
            log.debug('cannot assign value to reserved word')
            if 'any' in self.variables:
                del self.variables['any']
            if 'none' in self.variables:
                del self.variables['none']

        for pattern in contents.get('patterns'):
            pattern = self.add_pattern(pattern)

    def add_pattern(self, pattern):
        try:
            obj = Pattern(**pattern)

            if self.variables is not None:
                for item in obj.interfaces:
                    if item.device not in [None, 'any'] and \
                        item.device in self.variables:
                        item.device = self.variables[item.device]

        except TypeError:
            log.debug('Unable to parse pattern entry')
            return

        if 'node' in pattern:
            self.patterns['nodes'][obj.node] = obj
        else:
            self.patterns['globals'].append(obj)


    def get_patterns(self, node):
        """ returns a list of possible patterns for a given node """

        log.debug("Searching for systemmac %s in patterns" % node.systemmac)
        log.debug("Available node patterns: %s" % self.patterns['nodes'].keys())

        if node.systemmac in self.patterns['nodes'].keys():
            pattern = self.patterns['nodes'].get(node.systemmac)
            log.debug("Returning node pattern[%s] for node[%s]" % \
                (pattern.name, node.systemmac))
            return [pattern]
        else:
            log.debug("Returning node pattern[globals] patterns for node[%s]"\
                % node.systemmac)
            return self.patterns['globals']

    def match_node(self, node):
        """ Returns a list of :py:class:`Pattern` classes satisfied
        by the :py:class:`Node` argument
        """

        matches = list()
        for pattern in self.get_patterns(node):
            log.debug('Attempting to match Pattern [%s]' % pattern.name)
            if pattern.match_node(node, self.variables):
                log.debug('Match for [%s] was successful' % pattern.name)
                matches.append(pattern)
            else:
                log.debug("Failed to match [%s]" % pattern.name)
        return matches


class PatternError(Exception):
    pass

class Pattern(object):

    def __init__(self, name, definition, **kwargs):

        self.name = name
        self.definition = definition

        self.node = kwargs.get('node')
        self.variables = kwargs.get('variables') or dict()

        self.interfaces = list()
        if 'interfaces' in kwargs:
            self.add_interfaces(kwargs['interfaces'])

    def load(self, filename, content_type=CONTENT_TYPE_YAML):
        try:
            log.debug("Loading pattern from %s" % filename)
            contents = serializer.deserialize(open(filename).read(),
                                              content_type)
            self.deserialize(contents)
        except IOError as exc:
            log.debug(exc)
            raise PatternError

    def deserialize(self, contents):
        self.name = contents.get('name')
        self.definition = contents.get('definition')

        self.node = contents.get('node')
        self.variables = contents.get('variables') or dict()

        self.interfaces = list()
        if 'interfaces' in contents:
            self.add_interfaces(contents.get('interfaces'))

    def serialize(self):
        data = dict(name=self.name, definition=self.definition)
        data['variables'] = self.variables or dict()

        if self.node:
            data['node'] = self.node

        interfaces = list()
        for entry in self.interfaces:
            interfaces.append({entry.interface: entry.serialize()})
        data['interfaces'] = interfaces
        return data

    def add_interface(self, interface, device, port, tags=None):
        self.interfaces.append(InterfacePattern(interface, device, port, tags))

    def add_interfaces(self, interfaces):
        for interface in interfaces:
            for key, values in interface.items():
                args = self._parse_interface(key, values)
                log.debug("Adding interface to pattern with args %s" % 
                          str(args))
                self.add_interface(*args) #pylint: disable=W0142

    def _parse_interface(self, interface, values):
        log.debug("parse_interface[%s]: %s" % (str(interface), str(values)))

        device = port = tags = None
        if isinstance(values, dict):
            device = values.get('device')
            port = values.get('port')
            tags = values.get('tags')

        if values == 'any' or device == 'any':
            device, port, tags = 'any', 'any', None

        elif values == 'none' or device == 'none':
            device, port, tags = None, None, None

        else:
            try:
                device, port = DEVICENAME_PARSER_RE.split(values)
            except ValueError:
                device, port = ANYDEVICE_PARSER_RE.split(values)
            port, tags = port.split(':') if ':' in port else (port, None)

        #perform variable substitution
        if device not in [None, 'any'] and device in self.variables:
            device = self.variables[device]

        return (interface, device, port, tags)

    def match_node(self, node, variables=None):
        if variables is None:
            variables = {}

        neighbors = node.neighbors.copy()

        for intf_pattern in self.interfaces:
            log.debug('Attempting to match %r' % intf_pattern)
            log.debug('Available neighbors: %s' % neighbors.keys())

            # check for device none
            if intf_pattern.device is None:
                log.debug("InterfacePattern device is 'none'")
                return intf_pattern.interface not in neighbors

            variables.update(self.variables)
            matches = intf_pattern.match_neighbors(neighbors, variables)
            if not matches:
                log.debug("InterfacePattern failed to match interface[%s]" \
                    % intf_pattern.interface)
                return False

            log.debug("InterfacePattern matched interfaces %s" % matches)
            for match in matches:
                log.debug("Removing interface %s from available pool" % match)
                del neighbors[match]

        return True

class InterfacePattern(object):

    def __init__(self, interface, device, port, tags=None):

        self.interface = interface
        self.interfaces = self.range(interface)
        self.device = device
        self.port = port
        self.ports = self.range(port)
        self.tags = tags or list()

    def __repr__(self):
        return "InterfacePattern(interface=%s, device=%s, port=%s)" % \
            (self.interface, self.device, self.port)

    def serialize(self):
        obj = dict()
        if self.device is None:
            obj['device'] = 'none'
        else:
            obj['device'] = self.device
            obj['port'] = self.port
        if self.tags is not None:
            obj['tags'] = self.tags
        return obj

    @classmethod
    def range(cls, interface_range):
        if interface_range is None:
            return list()
        elif not interface_range.startswith('Ethernet'):
            return [interface_range]

        indicies = re.split("[a-zA-Z]*", interface_range)[1]
        indicies = indicies.split(',')

        interfaces = list()
        for index in indicies:
            if '-' in index:
                start, stop = index.split('-')
                for index in range(int(start), int(stop)+1):
                    interfaces.append('Ethernet%s' % index)
            else:
                interfaces.append('Ethernet%s' % index)
        return interfaces

    def match_neighbors(self, neighbors, variables):
        if self.interface == 'any':
            interfaces = neighbors()
        else:
            interfaces = [x for x in self.interfaces if x in neighbors()]

        matches = list()
        for interface in interfaces:
            for device, port in neighbors(interface):
                if self.match_device(device, variables) and \
                   self.match_port(port):
                    matches.append(interface)
                    break
        if matches != interfaces and self.interface != 'any':
            matches = list()
        return matches[0:1]

    def match_device(self, device, variables=None):
        if variables is None:
            variables = {}
        if self.device is None:
            return False
        elif self.device == 'any':
            return True
        pattern = variables.get(self.device) or self.device
        match = FUNC_RE.match(pattern)
        method = match.group('function') if match else 'exact'
        method = getattr(Functions, method)
        arg = match.group('arg') if match else pattern
        return method(arg, device)

    def match_port(self, port):
        if (self.port is None and self.device == 'any') or \
           (self.port == 'any'):
            return True
        elif self.port is None and self.device is None:
            return False
        return port in self.ports


def create_node(nodeattrs):
    node = Node(**nodeattrs)
    if node.systemmac is not None:
        node.systemmac = node.systemmac.replace(':', '')
    log.debug("Created node object %r" % node)
    return node

neighbordb = Topology()

def clear():
    global neighbordb       # pylint: disable=W0603
    neighbordb = Topology()

def default_filename():
    filepath = ztpserver.config.runtime.default.data_root
    filename = ztpserver.config.runtime.neighbordb.filename
    return os.path.join(filepath, filename)

def loads(data, content_type=CONTENT_TYPE_YAML):
    clear()
    neighbordb.loads(data, content_type)
    log.debug("Loaded neighbordb [%r]" % neighbordb)

def load(filename=None, content_type=CONTENT_TYPE_YAML):
    if filename is None:
        filename = default_filename()
    loads(open(filename).read(), content_type)

def resources(attributes, node):
    log.debug("Start processing resources with attributes: %s" % attributes)
    _attributes = dict()
    _resources = Resources()
    for key, value in attributes.items():
        if isinstance(value, dict):
            value = resources(value, node)
        elif isinstance(value, list):
            _value = list()
            for item in value:
                match = FUNC_RE.match(item)
                if match:
                    method = getattr(_resources, match.group('function'))
                    _value.append(method(match.group('arg'), node))
                else:
                    _value.append(item)
            value = _value
        elif isinstance(value, str):
            match = FUNC_RE.match(value)
            if match:
                method = getattr(_resources, match.group('function'))
                value = method(match.group('arg'), node)
                log.debug('Allocated value %s for attribute %s from pool %s' % \
                    (value, key, match.group('arg')))
        log.debug("Setting %s to %s" % (key, value))
        _attributes[key] = value
    return _attributes
