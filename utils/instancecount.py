#!/usr/bin/env python2
# -*- coding: iso-8859-1 -*-

import types
import StringIO


class InstanceCount:
    def __init__(self, in_obj):
        self.result = {}
        self.log = StringIO.StringIO()
        self._objcount(in_obj)

    def _objcount(self, in_obj):
        in_obj_str = str(in_obj)
        if len(in_obj_str) > 70:
            in_obj_str = in_obj_str[:70] + "..."

            #        print >> self.log, "objcount called with '%s', %s" % (in_obj_str, type(in_obj))
        if type(in_obj) == types.TupleType or type(in_obj) == types.ListType:
            for obj in in_obj:
                self._objcount(obj)
        elif type(in_obj) == types.DictType:
            for key in in_obj.keys():
                obj = in_obj[key]
                self._objcount(obj)
        elif type(in_obj) == types.InstanceType:
            #            print >> self.log, "found instance", in_obj_str
            self.add2result(in_obj.__class__, in_obj)
            # Recurse
            for attr in in_obj.__dict__.keys():
                obj = in_obj.__dict__[attr]
                self._objcount(obj)
        else:
            #            print >> self.log, "ignoring", in_obj_str
            pass

    def add2result(self, klass, instance):
        klass_id = id(klass)
        if not self.result.has_key(klass_id):
            # Does not exist in dictionary
            cinfo = ClassInfo(klass)
            self.result[klass_id] = cinfo
        else:
            # Already exists
            cinfo = self.result[klass_id]
            
        cinfo.add_instance(instance)

    def summary(self):
        print "Class  Nr. instances of this class"
        print "=================================="
        for klass_id in self.result.keys():
            cinfo = self.result[klass_id]
            print cinfo.klass, cinfo.get_num_instances()


class ClassInfo:
    def __init__(self, klass):
        self.klass = klass
        self.instances = []

    def add_instance(self, instance):
        instance_id = id(instance)
        if not instance_id in self.instances:
            self.instances.append(instance_id)

    def get_num_instances(self):
        return len(self.instances)


        
        



            
