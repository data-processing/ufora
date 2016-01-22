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


import pyfora.algorithms.regressionTrees.losses as losses
import pyfora.algorithms.regressionTrees.RegressionTree as RegressionTree
import pyfora.typeConverters.PurePandas as PurePandas


import math


class BinaryClassificationModel:
    """
    A class representing a gradient-boosted binary classification tree 
    model fit to data.
    """
    def __init__(
            self,
            additiveRegressionTree,
            X,
            classes,
            XDimensions,
            yAsSeries,
            loss,
            baseModelBuilder,
            learningRate
            ):
        self.additiveRegressionTree = additiveRegressionTree
        self.X = X
        self.classes = classes
        self.XDimensions = XDimensions
        self.yAsSeries = yAsSeries
        self.loss = loss
        self.baseModelBuilder = baseModelBuilder
        self.learningRate = learningRate

    @staticmethod
    def getInitialModel(
            X,
            yAsSeries,
            classes,
            learningRate,
            treeBuilderArgs
            ):
        loss = losses.BinomialLoss()

        additiveRegressionTree = loss.initialModel(classes, yAsSeries)

        XDimensions = range(X.shape[1])
        
        baseModelBuilder = RegressionTree.RegressionTreeBuilder(
            treeBuilderArgs.maxDepth,
            treeBuilderArgs.minSamplesSplit,
            treeBuilderArgs.numBuckets
            )

        return BinaryClassificationModel(
            additiveRegressionTree,
            X,
            classes,
            XDimensions,
            yAsSeries,
            loss,
            baseModelBuilder,
            learningRate
            )
            
    def predictionFunction_(self, row):
        if self.predictProbaFun_(row) >= 0.5:
            return self.classes[0]

        return self.classes[1]

    def predictProbaFun_(self, row):
        return 1.0 / (1.0 + math.exp(2.0 * self.additiveRegressionTree.predict(row)))

    def predictProbability(self, df):
        """
        Return class-zero probability estimates of the rows of a dataframe `df`. 
        """
        return df.apply(self.predictProbaFun_, 1)

    def pseudoResidualsAndRegressionValues(self, previousRegressionValues=None):
        if previousRegressionValues is None:
            regressionValues = self.additiveRegressionTree.predict(self.X)
        else:
            regressionValues = previousRegressionValues + \
                self.additiveRegressionTree.getTree(-1).predict(self.X)

        return (
            self.loss.negativeGradient(
                self.yAsSeries,
                regressionValues,
                self.classes
                ),
            regressionValues
            )

    def boost(self, pseudoResiduals):
        localX = self.X
        yDim = localX.shape[1]

        nextRegressionTree = self.baseModelBuilder.fit_(
            localX.pyfora_addColumn("__pseudoResiduals", pseudoResiduals),
            yDim,
            None,
            self.XDimensions,
            self.loss.leafValueFun(self.learningRate),
            None
            )
        
        return BinaryClassificationModel(
            self.additiveRegressionTree + nextRegressionTree,
            self.X,
            self.classes,
            self.XDimensions,
            self.yAsSeries,
            self.loss,
            self.baseModelBuilder,
            self.learningRate
            )

    def predict(self, df):
        """
        Use the classifier `self` to predict the class labels of the rows 
        of `df`
        """
        return df.apply(self.predictionFunction_, 1)

    def score(self, x, yTrue):
        """
        Compute the mean accuracy of the classifier `self` in predicting `x` 
        with respect to `yTrue`.
        """
        assert len(x) == len(yTrue)

        if isinstance(yTrue, PurePandas.PurePythonDataFrame):
            yTrue = yTrue.iloc[:, 0]

        def eltAt(ix):
            prediction = self.predictionFunction_(x.iloc[ix])
            if prediction == yTrue[ix]:
                return 1.0
            return 0.0

        return sum(eltAt(ix) for ix in xrange(len(x))) / len(x)

    def deviance(self, x, yTrue):
        """
        Compute the binomial deviance (average negative log-likihood) of the 
        instances in predictors `X` with responses `y`.
        """
        assert len(x) == len(yTrue)

        if isinstance(yTrue, PurePandas.PurePythonDataFrame):
            yTrue = yTrue.iloc[:, 0]

        probabilities = self.predictProbability(x)
        classZero = self.classes[0]
        
        def eltAt(ix):
            p = probabilities[ix]
            if yTrue[ix] == classZero:
                return math.log(p)
            else:
                return math.log(1.0 - p)

        return -sum(eltAt(ix) for ix in xrange(len(x))) / len(x)

    def featureImportances(self):
        raise NotImplementedError()

    
