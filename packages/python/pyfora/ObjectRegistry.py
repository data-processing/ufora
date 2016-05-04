#   Copyright 2015 Ufora Inc.
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

import pyfora.LongTermObjectRegistry as LongTermObjectRegistry
import pyfora.LongTermObjectRegistryIncrement as LongTermObjectRegistryIncrement
import pyfora.TypeDescription as TypeDescription
import base64


class ObjectRegistry(object):
    def __init__(self, longTermObjectRegistry=None):
        self._nextObjectID = 0

        # contains objects already defined on the server, which
        # we assume don't change, like files and class definitions
        if longTermObjectRegistry is None:
            longTermObjectRegistry = \
                LongTermObjectRegistry.LongTermObjectRegistry()
        self.longTermObjectRegistry = longTermObjectRegistry

        # a dict { objectId: ObjectDefinition } of objects eventually
        # to be merged into longTermRegistry. gets merged on calls to 
        # self.onServerUpdated
        self.longTermObjectRegistryIncrement = \
            LongTermObjectRegistryIncrement.LongTermObjectRegistryIncrement()

        # holds objects which are not in the longTermObjectRegistry
        # gets purged on calls to self.reset()
        self.shortTermObjectIdToObjectDefinition = {}

    def onServerUpdated(self):
        self.longTermObjectRegistry.mergeIncrement(
            self.longTermObjectRegistryIncrement)
        self.shortTermObjectIdToObjectDefinition = {}

    def longTermObjectId(self, pyObject):
        try:
            if self.longTermObjectRegistry.hasObject(pyObject):
                return self.longTermObjectRegistry.getObject(pyObject).objectId
            elif self.longTermObjectRegistryIncrement.hasObject(pyObject):
                return self.longTermObjectRegistryIncrement.getObject(pyObject).objectId
        except TypeError: # unhashable type
            return None

    def getDefinition(self, objectId):
        if self.longTermObjectRegistry.hasObjectId(objectId):
            return self.longTermObjectRegistry\
                       .getObjectDefinitionByObjectId(objectId)
        elif self.longTermObjectRegistryIncrement.hasObjectId(objectId):
            return self.longTermObjectRegistryIncrement\
                       .getObjectDefinitionByObjectId(objectId)

        return self.shortTermObjectIdToObjectDefinition[objectId]

    def allocateObject(self):
        "get a unique id for an object to be inserted later in the registry"
        objectId = self._nextObjectID
        self._nextObjectID += 1
        return objectId

    def idForFileAndText(self, path, text):
        longTermObjectIdOrNone = self.longTermObjectId(path)
        if longTermObjectIdOrNone is not None:
            return longTermObjectIdOrNone

        objectId = self.allocateObject()
        objectDefinition = TypeDescription.File(path, text)

        self.longTermObjectRegistryIncrement.pushIncrementEntry(
            path, objectId, objectDefinition)

        return objectId

    def pushLongtermObject(self, pyObject, objectId):
        self.longTermObjectRegistryIncrement.pushIncrementEntry(
            pyObject,
            objectId,
            self.getDefinition(objectId)
            )

    def definePrimitive(self, objectId, primitive, isLongTerm):
        if isinstance(primitive, str):
            primitive = base64.b64encode(primitive)

        if isLongTerm or isinstance(primitive, (type(None), bool)):
            self.longTermObjectRegistryIncrement.pushIncrementEntry(
                primitive,
                objectId,
                primitive
                )
        else:
            self.shortTermObjectIdToObjectDefinition[objectId] = primitive

    def defineTuple(self, pyObject, objectId, memberIds, isLongTerm):
        objectDefinition = TypeDescription.Tuple(memberIds)
        if isLongTerm:
            self.longTermObjectRegistryIncrement.pushIncrementEntry(
                pyObject,
                objectId,
                objectDefinition
                )
        else:
            self.shortTermObjectIdToObjectDefinition[objectId] = \
                objectDefinition

    def defineList(self, objectId, memberIds):
        self.shortTermObjectIdToObjectDefinition[objectId] = \
            TypeDescription.List(memberIds)

    def defineDict(self, objectId, keyIds, valueIds):
        self.shortTermObjectIdToObjectDefinition[objectId] = \
            TypeDescription.Dict(keyIds=keyIds,
                                 valueIds=valueIds)

    def defineRemotePythonObject(self, objectId, computedValueArg, isLongTerm=False):
        objectDefinition = TypeDescription.RemotePythonObject(computedValueArg)
        if isLongTerm:
            self.longTermObjectRegistryIncrement.pushIncrementEntry(
                computedValueArg,
                objectId,
                objectDefinition
                )
        else:
            self.shortTermObjectIdToObjectDefinition[objectId] = objectDefinition

    def defineBuiltinExceptionInstance(self,
                                       objectId,
                                       builtinExceptionInstance,
                                       typename,
                                       argsId):
        self.longTermObjectRegistryIncrement.pushIncrementEntry(
            builtinExceptionInstance,
            objectId,
            TypeDescription.BuiltinExceptionInstance(typename, argsId)
            )

    def defineNamedSingleton(self, objectId, singletonName, pyObject):
        self.longTermObjectRegistryIncrement.pushIncrementEntry(
            pyObject,
            objectId,
            TypeDescription.NamedSingleton(singletonName)
            )

    def defineFunction(self,
                       function,
                       objectId,
                       sourceFileId,
                       lineNumber,
                       scopeIds,
                       isLongTerm):
        """
        scopeIds: a dict freeVariableMemberAccessChain -> id
        """
        objectDefinition = TypeDescription.FunctionDefinition(
            sourceFileId=sourceFileId,
            lineNumber=lineNumber,
            freeVariableMemberAccessChainsToId=scopeIds
            )

        if isLongTerm:
            self.longTermObjectRegistryIncrement.pushIncrementEntry(
                function,
                objectId,
                objectDefinition
                )
        else:
            self.shortTermObjectIdToObjectDefinition[objectId] = \
                objectDefinition

    def defineClass(self,
                    cls,
                    objectId,
                    sourceFileId,
                    lineNumber,
                    scopeIds,
                    baseClassIds,
                    isLongTerm):
        """
        scopeIds: a dict freeVariableMemberAccessChain -> id
        baseClassIds: a list of ids representing (immediate) base classes
        """
        objectDefinition = TypeDescription.ClassDefinition(
            sourceFileId=sourceFileId,
            lineNumber=lineNumber,
            freeVariableMemberAccessChainsToId=scopeIds,
            baseClassIds=baseClassIds
            )

        if isLongTerm:
            self.longTermObjectRegistryIncrement.pushIncrementEntry(
                cls, objectId, objectDefinition
                )
        else:
            self.shortTermObjectIdToObjectDefinition[objectId] = objectDefinition


    def defineUnconvertible(self, objectId):
        self.shortTermObjectIdToObjectDefinition[objectId] = \
            TypeDescription.Unconvertible()

    def defineClassInstance(self,
                            pyObject,
                            objectId,
                            classId,
                            classMemberNameToClassMemberId,
                            isLongTerm):
        objectDefinition = TypeDescription.ClassInstanceDescription(
            classId=classId,
            classMemberNameToClassMemberId=classMemberNameToClassMemberId
            )

        if isLongTerm:
            self.longTermObjectRegistryIncrement.pushIncrementEntry(
                pyObject,
                objectId,
                objectDefinition
                )
        else:
            self.shortTermObjectIdToObjectDefinition[objectId] = \
                objectDefinition

    def defineInstanceMethod(self,
                             pyObject,
                             objectId,
                             instanceId,
                             methodName,
                             isLongTerm):
        objectDefinition = TypeDescription.InstanceMethod(
            instanceId=instanceId,
            methodName=methodName
            )
        
        if isLongTerm:
            self.longTermObjectRegistryIncrement.pushIncrementEntry(
                pyObject,
                objectId,
                objectDefinition
                )
        else:
            self.shortTermObjectIdToObjectDefinition[objectId] = \
                objectDefinition

    def defineWithBlock(self,
                        objectId,
                        freeVariableMemberAccessChainsToId,
                        sourceFileId,
                        lineNumber):
        self.shortTermObjectIdToObjectDefinition[objectId] = \
            TypeDescription.WithBlockDescription(
                freeVariableMemberAccessChainsToId,
                sourceFileId,
                lineNumber
                )

    def computeDependencyGraph(self, objectId):
        graphOfIds = dict()
        self._populateGraphOfIds(graphOfIds, objectId)
        return graphOfIds

    def _populateGraphOfIds(self, graphOfIds, objectId):
        dependentIds = self._computeDependentIds(objectId)
        graphOfIds[objectId] = dependentIds

        for objectId in dependentIds:
            if objectId not in graphOfIds:
                self._populateGraphOfIds(graphOfIds, objectId)

    def _computeDependentIds(self, objectId):
        objectDefinition = self.getDefinition(objectId)

        if TypeDescription.isPrimitive(objectDefinition) or \
                isinstance(objectDefinition,
                           (TypeDescription.File, TypeDescription.RemotePythonObject,
                            TypeDescription.NamedSingleton, list,
                            TypeDescription.Unconvertible)):
            return []
        elif isinstance(objectDefinition, (TypeDescription.BuiltinExceptionInstance)):
            return [objectDefinition.argsId]
        elif isinstance(objectDefinition, (TypeDescription.List, TypeDescription.Tuple)):
            return objectDefinition.memberIds
        elif isinstance(objectDefinition,
                        (TypeDescription.FunctionDefinition, TypeDescription.ClassDefinition)):
            tr = objectDefinition.freeVariableMemberAccessChainsToId.values()
            tr.append(objectDefinition.sourceFileId)
            return tr
        elif isinstance(objectDefinition, TypeDescription.InstanceMethod):
            return [objectDefinition.instanceId]
        elif isinstance(objectDefinition, TypeDescription.ClassInstanceDescription):
            tr = [objectDefinition.classId]
            tr.extend(
                self._computeDependentIds(
                    objectDefinition.classId
                    )
                )
            classMemberIds = \
                objectDefinition.classMemberNameToClassMemberId.values()
            tr.extend(classMemberIds)

            return tr
        elif isinstance(objectDefinition, TypeDescription.Dict):
            return objectDefinition.keyIds + objectDefinition.valueIds
        elif isinstance(objectDefinition, TypeDescription.WithBlockDescription):
            tr = objectDefinition.freeVariableMemberAccessChainsToId.values()
            tr.append(objectDefinition.sourceFileId)

            return tr
        else:
            assert False, "don't know what to do with %s" % type(objectDefinition)

