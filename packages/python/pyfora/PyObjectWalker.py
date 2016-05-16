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
import pyfora
import pyfora.Exceptions as Exceptions
import pyfora.Future as Future
import pyfora.NamedSingletons as NamedSingletons
import pyfora.pyAst.PyAstFreeVariableAnalyses as PyAstFreeVariableAnalyses
import pyfora.pyAst.PyAstUtil as PyAstUtil
import pyfora.PyforaInspect as PyforaInspect
import pyfora.PyforaWithBlock as PyforaWithBlock
import pyfora.RemotePythonObject as RemotePythonObject

from pyfora.TypeDescription import isPrimitive
from pyfora.PyforaInspect import PyforaInspectError

import logging
import traceback
import __builtin__
import ast
import sys


class UnresolvedFreeVariableException(Exception):
    def __init__(self, freeVariable, contextName):
        super(UnresolvedFreeVariableException, self).__init__()
        self.freeVarChainWithPos = freeVariable
        self.contextNameOrNone = contextName


class UnresolvedFreeVariableExceptionWithTrace(Exception):
    def __init__(self, message, trace=None):
        super(UnresolvedFreeVariableExceptionWithTrace, self).__init__()
        self.message = message
        if trace is None:
            self.trace = []
        else:
            self.trace = trace
    def addToTrace(self, elmt):
        Exceptions.checkTraceElement(elmt)
        self.trace.insert(0, elmt)


def _convertUnresolvedFreeVariableExceptionAndRaise(e, sourceFileName):
    logging.error(
        "Converter raised an UnresolvedFreeVariableException exception: %s",
        traceback.format_exc())
    chainWithPos = e.freeVarChainWithPos
    varLine = chainWithPos.pos.lineno
    varName = chainWithPos.var[0]
    raise UnresolvedFreeVariableExceptionWithTrace(
        '''unable to resolve free variable '%s' for pyfora conversion''' % varName,
        [Exceptions.makeTraceElement(sourceFileName, varLine)]
        )


def isClassInstance(pyObject):
    return hasattr(pyObject, "__class__")


class _AClassWithAMethod(object):
    def f(self):
        pass


instancemethod = type(_AClassWithAMethod().f)


builtin_function_or_method = type(isinstance)


class _Unconvertible(object):
    pass


class _FunctionDefinition(object):
    def __init__(self, sourceFileId, lineNumber, freeVariableMemberAccessChainsToId):
        self.sourceFileId = sourceFileId
        self.lineNumber = lineNumber
        self.freeVariableMemberAccessChainsToId = \
            freeVariableMemberAccessChainsToId


class _ClassDefinition(object):
    def __init__(self, sourceFileId, lineNumber, freeVariableMemberAccessChainsToId):
        self.sourceFileId = sourceFileId
        self.lineNumber = lineNumber
        self.freeVariableMemberAccessChainsToId = \
            freeVariableMemberAccessChainsToId


class PyObjectWalker(object):
    """
    `PyObjectWalker`: walk a live python object, registering its pieces with an
    `ObjectRegistry`

    The main, and only publicly viewable function on this class is `walkPyObject`

    Attributes:
        _`purePythonClassMapping`: a `PureImplementationMapping` -- used to
            "replace" python objects in an python object graph by a "Pure"
            python class. For example, treat this `np.array` as a
        `PurePython.SomePureImplementationOfNumpyArray`.
        `_convertedObjectCache`: a mapping from python id -> pure instance
        `_pyObjectIdToObjectId`: mapping from python id -> id registered in
            `self.objectRegistry`
        `_objectRegistry`: an `ObjectRegistry` which holds an image of the
            objects we visit.

    """
    def __init__(self, purePythonClassMapping, objectRegistry):
        assert purePythonClassMapping is not None

        for singleton in NamedSingletons.pythonSingletonToName:
            if purePythonClassMapping.canMap(singleton):
                raise UserWarning(
                    "You provided a mapping that applies to %s, "
                    "which already has a direct mapping" % singleton
                    )

        self._purePythonClassMapping = purePythonClassMapping
        self._convertedObjectCache = {}
        self._pyObjectIdToObjectId = {}
        self._objectRegistry = objectRegistry

    def _allocateId(self, pyObject):
        objectId = self._objectRegistry.allocateObject()
        self._pyObjectIdToObjectId[id(pyObject)] = objectId

        return objectId

    def walkPyObject(self, pyObject, isLongTerm=False):
        """
        `walkPyObject`: recursively traverse a live python object,
        registering its "pieces" with an `ObjectRegistry`
        (`self.objectRegistry`).

        Note that we use python `id`s for caching in this class,
        which means it cannot be used in cases where `id`s might get
        reused (recall they are just memory addresses).

        `objectId`s are assigned to all pieces of the python object.

        Returns:
            An `int`, the `objectId` of the root python object.
        """

        originalPyObject = pyObject

        objectIdOrNone = self._objectRegistry.longTermObjectId(pyObject)
        if objectIdOrNone is not None:
            print "got an objectId %s for pyObject %s" % (objectIdOrNone, pyObject)
            if objectIdOrNone == 32:
                print self._objectRegistry.getDefinition(32)
                print "id(pyObject) = ", id(pyObject)
                print
            return objectIdOrNone

        if id(pyObject) in self._pyObjectIdToObjectId:
            return self._pyObjectIdToObjectId[id(pyObject)]

        if id(pyObject) in self._convertedObjectCache:
            pyObject = self._convertedObjectCache[id(pyObject)]
        elif self._purePythonClassMapping.canMap(pyObject):
            pureInstance = self._purePythonClassMapping.mappableInstanceToPure(
                pyObject
                )
            self._convertedObjectCache[id(pyObject)] = pureInstance

            if self._purePythonClassMapping.isMappableInstance(pyObject):
                isLongTerm = True

            pyObject = pureInstance

        objectId = self._allocateId(pyObject)

        if pyObject is pyfora.connect:
            self._registerUnconvertible(objectId)
            return objectId

        try:
            self._walkPyObject(pyObject, objectId, isLongTerm=isLongTerm)
        except Exceptions.CantGetSourceTextError:
            self._registerUnconvertible(objectId)
        except PyforaInspectError:
            self._registerUnconvertible(objectId)

        return objectId

    def _walkPyObject(self, pyObject, objectId, isLongTerm=False):
        if isinstance(pyObject, RemotePythonObject.RemotePythonObject):
            self._registerRemotePythonObject(objectId, pyObject, isLongTerm=isLongTerm)
        elif isinstance(pyObject, Future.Future):
            #it would be better to register the future and do a second pass of walking
            self._walkPyObject(pyObject.result(), objectId, isLongTerm=isLongTerm)
        elif isinstance(pyObject, Exception) and pyObject.__class__ in \
           NamedSingletons.pythonSingletonToName:
            self._registerBuiltinExceptionInstance(objectId, pyObject)
        elif isinstance(pyObject, (type, builtin_function_or_method)) and \
           pyObject in NamedSingletons.pythonSingletonToName:
            self._registerNamedSingleton(
                objectId,
                NamedSingletons.pythonSingletonToName[pyObject],
                pyObject
                )
        elif isinstance(pyObject, PyforaWithBlock.PyforaWithBlock):
            assert not isLongTerm, "withBlocks shouldn't be long term"
            self._registerWithBlock(objectId, pyObject)
        elif isinstance(pyObject, _Unconvertible):
            self._registerUnconvertible(objectId)
        elif isinstance(pyObject, tuple):
            self._registerTuple(objectId, pyObject, isLongTerm=isLongTerm)
        elif isinstance(pyObject, list):
            assert not isLongTerm, \
                "lists cannot be long term objects, as they are unhashable"
            self._registerList(objectId, pyObject)
        elif isinstance(pyObject, dict):
            assert not isLongTerm, \
                "dicts cannot be long term objects, as they are unhashable"
            self._registerDict(objectId, pyObject)
        elif isPrimitive(pyObject):
            self._registerPrimitive(objectId, pyObject, isLongTerm=isLongTerm)
        elif PyforaInspect.isfunction(pyObject):
            if not isLongTerm:
                isLongTerm = self._isLongTermFunction(pyObject)
            self._registerFunction(objectId, pyObject, isLongTerm=isLongTerm)
        elif PyforaInspect.isclass(pyObject):
            if not isLongTerm:
                isLongTerm = self._isLongTermClass(pyObject)
            self._registerClass(objectId, pyObject, isLongTerm=isLongTerm)
        elif isinstance(pyObject, instancemethod):
            self._registerInstanceMethod(objectId, pyObject, isLongTerm=isLongTerm)
        elif isClassInstance(pyObject):
            self._registerClassInstance(objectId, pyObject, isLongTerm)
        else:
            assert False, "don't know what to do with %s" % pyObject

    def _isLongTermFunction(self, function):
        if hasattr(function, "__module__"):
            moduleName = function.__module__
            if moduleName == "__main__":
                return False
            module = sys.modules[moduleName]

            return len(PyforaInspect.getmembers(
                module,
                lambda _: (PyforaInspect.ismethod(_) or PyforaInspect.isfunction(_)) \
                and _.__name__ == function.__name__
                )) > 0

        return False

    def _isLongTermClass(self, cls):
        if hasattr(cls, "__module__"):
            moduleName = cls.__module__
            if moduleName == "__main__":
                return False
            module = sys.modules[moduleName]

            return len(PyforaInspect.getmembers(
                module,
                lambda _: PyforaInspect.isclass(_) and _.__name__ == cls.__name__
                )) > 0

    def _registerRemotePythonObject(self, objectId, remotePythonObject, isLongTerm):
        """
        `_registerRemotePythonObject`: register a remotePythonObject
        (a terminal node in a python object graph) with `self.objectRegistry`
        """
        self._objectRegistry.defineRemotePythonObject(
            objectId,
            remotePythonObject._pyforaComputedValueArg(),
            isLongTerm=isLongTerm
            )

    def _registerBuiltinExceptionInstance(self, objectId, builtinExceptionInstance):
        """
        `_registerBuiltinExceptionInstance`: register a `builtinExceptionInstance`
        with `self.objectRegistry`.

        Recursively call `walkPyObject` on the args of the instance.
        """
        argsId = self.walkPyObject(builtinExceptionInstance.args)

        self._objectRegistry.defineBuiltinExceptionInstance(
            objectId,
            builtinExceptionInstance,
            NamedSingletons.pythonSingletonToName[
                builtinExceptionInstance.__class__
                ],
            argsId
            )

    def _registerNamedSingleton(self, objectId, singletonName, pyObject):
        """
        `_registerNamedSingleton`: register a `NamedSingleton`
        (a terminal node in a python object graph) with `self.objectRegistry`
        """
        self._objectRegistry.defineNamedSingleton(objectId, singletonName, pyObject)

    def _registerTuple(self, objectId, tuple_, isLongTerm):
        """
        `_registerTuple`: register a `tuple` instance
        with `self.objectRegistry`.

        Recursively call `walkPyObject` on the values in the tuple.
        """
        memberIds = [self.walkPyObject(val, isLongTerm=isLongTerm) for val in tuple_]

        self._objectRegistry.defineTuple(
            tuple_,
            objectId=objectId,
            memberIds=memberIds,
            isLongTerm=isLongTerm
            )

    def _registerList(self, objectId, list_):
        """
        `_registerList`: register a `list` instance with `self.objectRegistry`.
        Recursively call `walkPyObject` on the values in the list.
        """
        if all(isPrimitive(val) for val in list_):
            self._registerPrimitive(objectId, list_)
        else:
            memberIds = [self.walkPyObject(val) for val in list_]

            self._objectRegistry.defineList(
                objectId=objectId,
                memberIds=memberIds
                )

    def _registerPrimitive(self, objectId, primitive, isLongTerm=False):
        """
        `_registerPrimitive`: register a primitive (defined by `isPrimitive`)
        (a terminal node in a python object graph) with `self.objectRegistry`
        """
        self._objectRegistry.definePrimitive(
            objectId,
            primitive,
            isLongTerm
            )

    def _registerDict(self, objectId, dict_):
        """
        `_registerDict`: register a `dict` instance
        with `self.objectRegistry`.

        Recursively call `walkPyObject` on the keys and values in the dict
        """
        keyIds, valueIds = [], []
        for k, v in dict_.iteritems():
            keyIds.append(self.walkPyObject(k, isLongTerm=isLongTerm))
            valueIds.append(self.walkPyObject(v, isLongTerm=isLongTerm))

        self._objectRegistry.defineDict(
            objectId=objectId,
            keyIds=keyIds,
            valueIds=valueIds
            )

    def _registerInstanceMethod(self, objectId, pyObject, isLongTerm):
        """
        `_registerInstanceMethod`: register an `instancemethod` instance
        with `self.objectRegistry`.

        Recursively call `walkPyObject` on the object to which the instance is
        bound, and encode alongside the name of the method.
        """
        instance = pyObject.__self__
        methodName = pyObject.__name__

        instanceId = self.walkPyObject(instance)

        self._objectRegistry.defineInstanceMethod(
            pyObject,
            objectId=objectId,
            instanceId=instanceId,
            methodName=methodName,
            isLongTerm=isLongTerm
            )


    def _registerClassInstance(self, objectId, classInstance, isLongTerm):
        """
        `_registerClassInstance`: register a `class` instance
        with `self.objectRegistry`.

        Recursively call `walkPyObject` on the class of the `classInstance`
        and on the data members of the instance.
        """
        classObject = classInstance.__class__
        classId = self.walkPyObject(classObject, isLongTerm=isLongTerm)

        dataMemberNames = classInstance.__dict__.keys() if hasattr(classInstance, '__dict__') \
            else PyAstUtil.collectDataMembersSetInInit(classObject)
        classMemberNameToClassMemberId = {}
        for dataMemberName in dataMemberNames:
            memberId = self.walkPyObject(getattr(classInstance, dataMemberName))
            classMemberNameToClassMemberId[dataMemberName] = memberId

        self._objectRegistry.defineClassInstance(
            classInstance,
            objectId=objectId,
            classId=classId,
            classMemberNameToClassMemberId=classMemberNameToClassMemberId,
            isLongTerm=isLongTerm
            )

    def _registerWithBlock(self, objectId, pyObject):
        """
        `_registerWithBlock`: register a `PyforaWithBlock.PyforaWithBlock`
        with `self.objectRegistry`.

        Recursively call `walkPyObject` on the resolvable free variable
        member access chains in the block and on the file object.
        """
        lineNumber = pyObject.lineNumber
        sourceTree = PyAstUtil.pyAstFromText(pyObject.sourceText)
        withBlockAst = PyAstUtil.withBlockAtLineNumber(sourceTree, lineNumber)

        withBlockFun = ast.FunctionDef(
            name="",
            args=ast.arguments(args=[], defaults=[], kwarg=None, vararg=None),
            body=withBlockAst.body,
            decorator_list=[],
            lineno=lineNumber,
            col_offset=0
            )

        if PyAstUtil.hasReturnInOuterScope(withBlockFun):
            raise Exceptions.BadWithBlockError(
                "return statement not supported in pyfora with-block (line %s)" %
                PyAstUtil.getReturnLocationsInOuterScope(withBlockFun)[0])

        if PyAstUtil.hasYieldInOuterScope(withBlockFun):
            raise Exceptions.BadWithBlockError(
                "yield expression not supported in pyfora with-block (line %s)" %
                PyAstUtil.getYieldLocationsInOuterScope(withBlockFun)[0])

        freeVariableMemberAccessChainsWithPositions = \
            self._freeMemberAccessChainsWithPositions(withBlockFun)

        boundValuesInScopeWithPositions = \
            PyAstFreeVariableAnalyses.collectBoundValuesInScope(
                withBlockFun, getPositions=True)

        for boundValueWithPosition in boundValuesInScopeWithPositions:
            val, pos = boundValueWithPosition
            if val not in pyObject.unboundLocals and val in pyObject.boundVariables:
                freeVariableMemberAccessChainsWithPositions.add(
                    PyAstFreeVariableAnalyses.VarWithPosition(var=(val,), pos=pos)
                    )

        try:
            freeVariableMemberAccessChainResolutions = \
                self._resolveFreeVariableMemberAccessChains(
                    freeVariableMemberAccessChainsWithPositions, pyObject.boundVariables
                    )
        except UnresolvedFreeVariableException as e:
            _convertUnresolvedFreeVariableExceptionAndRaise(e, pyObject.sourceFileName)

        try:
            processedFreeVariableMemberAccessChainResolutions = {}
            for chain, (resolution, position) in \
                freeVariableMemberAccessChainResolutions.iteritems():
                processedFreeVariableMemberAccessChainResolutions['.'.join(chain)] = \
                    self.walkPyObject(resolution)
        except UnresolvedFreeVariableExceptionWithTrace as e:
            e.addToTrace(
                Exceptions.makeTraceElement(
                    path=pyObject.sourceFileName,
                    lineNumber=position.lineno
                    )
                )
            raise

        sourceFileId = self._idForFile(
            path=pyObject.sourceFileName,
            )

        self._objectRegistry.defineWithBlock(
            objectId=objectId,
            freeVariableMemberAccessChainsToId=\
                processedFreeVariableMemberAccessChainResolutions,
            sourceFileId=sourceFileId,
            lineNumber=lineNumber
            )

    def _idForFile(self, path, text=None):
        if text is None:
            text = "".join(PyforaInspect.getlines(path))

        return self._objectRegistry.idForFileAndText(path, text)

    def _registerFunction(self, objectId, function, isLongTerm):
        """
        `_registerFunction`: register a python function with `self.objectRegistry.

        Recursively call `walkPyObject` on the resolvable free variable member
        access chains in the function, as well as on the source file object.
        """
        functionDescription = self._classOrFunctionDefinition(
            function,
            classOrFunction=_FunctionDefinition,
            isLongTerm=isLongTerm
            )

        self._objectRegistry.defineFunction(
            function,
            objectId=objectId,
            sourceFileId=functionDescription.sourceFileId,
            lineNumber=functionDescription.lineNumber,
            scopeIds=functionDescription.freeVariableMemberAccessChainsToId,
            isLongTerm=isLongTerm
            )

    def _registerClass(self, objectId, pyObject, isLongTerm):
        """
        `_registerClass`: register a python class with `self.objectRegistry.

        Recursively call `walkPyObject` on the resolvable free variable member
        access chains in the class, as well as on the source file object.
        """
        classDescription = self._classOrFunctionDefinition(
            pyObject,
            classOrFunction=_ClassDefinition,
            isLongTerm=isLongTerm
            )
        assert all(id(base) in self._pyObjectIdToObjectId for base in pyObject.__bases__)

        self._objectRegistry.defineClass(
            cls=pyObject,
            objectId=objectId,
            sourceFileId=classDescription.sourceFileId,
            lineNumber=classDescription.lineNumber,
            scopeIds=classDescription.freeVariableMemberAccessChainsToId,
            baseClassIds=[self._pyObjectIdToObjectId[id(base)] for base in pyObject.__bases__],
            isLongTerm=isLongTerm
            )

    def _registerUnconvertible(self, objectId):
        self._objectRegistry.defineUnconvertible(objectId)

    def _classOrFunctionDefinition(self, pyObject, classOrFunction, isLongTerm):
        """
        `_classOrFunctionDefinition: create a `_FunctionDefinition` or
        `_ClassDefinition` out of a python class or function, recursively visiting
        the resolvable free variable member access chains in `pyObject` as well
        as the source file object.

        Args:
            `pyObject`: a python class or function.
            `classOrFunction`: should either be `_FunctionDefinition` or
                `_ClassDefinition`.

        Returns:
            a `_FunctionDefinition` or `_ClassDefinition`.

        """
        if pyObject.__name__ == '__inline_fora':
            raise Exceptions.PythonToForaConversionError(
                "in pyfora, '__inline_fora' is a reserved word"
                )

        sourceFileText, sourceFileName = PyAstUtil.getSourceFilenameAndText(pyObject)

        _, sourceLine = PyAstUtil.getSourceLines(pyObject)

        sourceAst = PyAstUtil.pyAstFromText(sourceFileText)

        if classOrFunction is _FunctionDefinition:
            pyAst = PyAstUtil.functionDefOrLambdaAtLineNumber(sourceAst, sourceLine)
        else:
            assert classOrFunction is _ClassDefinition
            pyAst = PyAstUtil.classDefAtLineNumber(sourceAst, sourceLine)

        assert sourceLine == pyAst.lineno

        try:
            freeVariableMemberAccessChainResolutions = \
                self._computeAndResolveFreeVariableMemberAccessChainsInAst(
                    pyObject, pyAst
                    )
        except UnresolvedFreeVariableException as e:
            _convertUnresolvedFreeVariableExceptionAndRaise(e, sourceFileName)

        try:
            processedFreeVariableMemberAccessChainResolutions = {}
            for chain, (resolution, location) in \
                freeVariableMemberAccessChainResolutions.iteritems():
                processedFreeVariableMemberAccessChainResolutions['.'.join(chain)] = \
                    self.walkPyObject(resolution, isLongTerm=isLongTerm)
        except UnresolvedFreeVariableExceptionWithTrace as e:
            e.addToTrace(
                Exceptions.makeTraceElement(
                    path=sourceFileName,
                    lineNumber=location[0]
                    )
                )
            raise

        sourceFileId = self._idForFile(
            path=sourceFileName,
            text=sourceFileText
            )

        return classOrFunction(
            sourceFileId=sourceFileId,
            lineNumber=sourceLine,
            freeVariableMemberAccessChainsToId=\
                processedFreeVariableMemberAccessChainResolutions
            )

    @staticmethod
    def _freeMemberAccessChainsWithPositions(pyAst):
        # ATz: just added 'False' as a 2nd argument, but we may need to check
        # that whenever pyAst is a FunctionDef node, its context is not a class
        # (i.e., it is not an instance method). In that case, we need to pass
        # 'True' as the 2nd argument.

        def is_pureMapping_call(node):
            return isinstance(node, ast.Call) and \
                isinstance(node.func, ast.Name) and \
                node.func.id == 'pureMapping'

        freeVariableMemberAccessChains = \
            PyAstFreeVariableAnalyses.getFreeVariableMemberAccessChains(
                pyAst,
                isClassContext=False,
                getPositions=True,
                exclude_predicate=is_pureMapping_call
                )

        return freeVariableMemberAccessChains

    def _resolveChainByDict(self, chainWithPosition, boundVariables):
        """
        `_resolveChainByDict`: look up a free variable member access chain, `chain`,
        in a dictionary of resolutions, `boundVariables`, or in `__builtin__` and
        return a tuple (subchain, resolution, location).
        """
        freeVariable = chainWithPosition.var[0]

        if freeVariable in boundVariables:
            rootValue = boundVariables[freeVariable]

        elif hasattr(__builtin__, freeVariable):
            rootValue = getattr(__builtin__, freeVariable)

        else:
            raise UnresolvedFreeVariableException(chainWithPosition, None)

        return self._computeSubchainAndTerminalValueAlongModules(
            rootValue, chainWithPosition)


    def _resolveChainInPyObject(self, chainWithPosition, pyObject, pyAst):
        """
        This name could be improved.

        Returns a `subchain, terminalPyValue, location` tuple: this represents
        the deepest value we can get to in the member chain `chain` on `pyObject`
        taking members only along modules (or "empty" modules)

        """
        subchainAndResolutionOrNone = self._subchainAndResolutionOrNone(pyObject,
                                                                        pyAst,
                                                                        chainWithPosition)
        if subchainAndResolutionOrNone is None:
            raise Exceptions.PythonToForaConversionError(
                "don't know how to resolve %s in %s (line:%s)"
                % (chainWithPosition.var, pyObject, chainWithPosition.pos.lineno)
                )

        subchain, terminalValue, location = subchainAndResolutionOrNone

        if id(terminalValue) in self._convertedObjectCache:
            terminalValue = self._convertedObjectCache[id(terminalValue)]

        return subchain, terminalValue, location

    def _subchainAndResolutionOrNone(self, pyObject, pyAst, chainWithPosition):
        if PyforaInspect.isfunction(pyObject):
            return self._lookupChainInFunction(pyObject, chainWithPosition)

        if PyforaInspect.isclass(pyObject):
            return self._lookupChainInClass(pyObject, pyAst, chainWithPosition)

        return None

    @staticmethod
    def _classMemberFunctions(pyObject):
        return PyforaInspect.getmembers(
            pyObject,
            lambda elt: PyforaInspect.ismethod(elt) or PyforaInspect.isfunction(elt)
            )

    def _lookupChainInClass(self, pyClass, pyAst, chainWithPosition):
        """
        return a pair `(subchain, subchainResolution)`
        where subchain resolves to subchainResolution in pyClass
        """
        memberFunctions = self._classMemberFunctions(pyClass)

        for _, func in memberFunctions:
            # lookup should be indpendent of which function we
            # actually choose. However, the unbound chain may not
            # appear in every member function
            try:
                return self._lookupChainInFunction(func, chainWithPosition)
            except UnresolvedFreeVariableException:
                pass

        baseClassResolutionOrNone = self._resolveChainByBaseClasses(
            pyClass, pyAst, chainWithPosition
            )
        if baseClassResolutionOrNone is not None:
            return baseClassResolutionOrNone

        raise UnresolvedFreeVariableException(chainWithPosition, None)

    def _resolveChainByBaseClasses(self, pyClass, pyAst, chainWithPosition):
        chain = chainWithPosition.var
        position = chainWithPosition.pos

        baseClassChains = [self._getBaseClassChain(base) for base in pyAst.bases]

        if chain in baseClassChains:
            resolution = pyClass.__bases__[baseClassChains.index(chain)]
            return chain, resolution, position

        # note: we could do better here. we could search the class
        # variables of the base class as well
        return None

    def _getBaseClassChain(self, baseAst):
        if isinstance(baseAst, ast.Name):
            return (baseAst.id,)
        if isinstance(baseAst, ast.Attribute):
            return self._getBaseClassChain(baseAst.value) + (baseAst.attr,)

    def _lookupChainInFunction(self, pyFunction, chainWithPosition):
        """
        return a tuple `(subchain, subchainResolution, location)`
        where subchain resolves to subchainResolution in pyFunction
        """
        freeVariable = chainWithPosition.var[0]

        if freeVariable in pyFunction.func_code.co_freevars:
            index = pyFunction.func_code.co_freevars.index(freeVariable)
            try:
                rootValue = pyFunction.__closure__[index].cell_contents
            except Exception as e:
                logging.error("Encountered Exception: %s: %s", type(e).__name__, e)
                logging.error(
                    "Failed to get value for free variable %s\n%s",
                    freeVariable, traceback.format_exc())
                raise UnresolvedFreeVariableException(
                    chainWithPosition, pyFunction.func_name)

        elif freeVariable in pyFunction.func_globals:
            rootValue = pyFunction.func_globals[freeVariable]

        elif hasattr(__builtin__, freeVariable):
            rootValue = getattr(__builtin__, freeVariable)

        else:
            raise UnresolvedFreeVariableException(
                chainWithPosition, pyFunction.func_name)

        return self._computeSubchainAndTerminalValueAlongModules(
            rootValue, chainWithPosition)

    @staticmethod
    def _computeSubchainAndTerminalValueAlongModules(rootValue, chainWithPosition):
        ix = 1
        chain = chainWithPosition.var
        position = chainWithPosition.pos

        subchain, terminalValue = chain[:ix], rootValue

        while PyforaInspect.ismodule(terminalValue):
            if ix >= len(chain):
                #we're terminating at a module
                terminalValue = _Unconvertible()
                break

            if not hasattr(terminalValue, chain[ix]):
                raise Exceptions.PythonToForaConversionError(
                    "Module %s has no member %s" % (str(terminalValue), chain[ix])
                    )

            terminalValue = getattr(terminalValue, chain[ix])
            ix += 1
            subchain = chain[:ix]

        return subchain, terminalValue, position

    def _resolveFreeVariableMemberAccessChains(self,
                                               freeVariableMemberAccessChainsWithPositions,
                                               boundVariables):
        """ Return a dictionary mapping subchains to resolved ids."""
        resolutions = dict()

        for chainWithPosition in freeVariableMemberAccessChainsWithPositions:
            subchain, resolution, position = self._resolveChainByDict(
                chainWithPosition, boundVariables)

            if id(resolution) in self._convertedObjectCache:
                resolution = self._convertedObjectCache[id(resolution)][1]

            resolutions[subchain] = (resolution, position)

        return resolutions

    def _computeAndResolveFreeVariableMemberAccessChainsInAst(self, pyObject, pyAst):
        resolutions = {}

        for chainWithPosition in self._freeMemberAccessChainsWithPositions(pyAst):
            if chainWithPosition and \
               chainWithPosition.var[0] in ['staticmethod', 'property', '__inline_fora']:
                continue

            subchain, resolution, position = self._resolveChainInPyObject(
                chainWithPosition, pyObject, pyAst
                )
            resolutions[subchain] = (resolution, position)

        return resolutions

