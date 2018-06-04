# -*- coding: utf-8 -*-
# %%

from __future__ import division
from __future__ import print_function

import numpy as np
import numbers
from sklearn.utils.validation import check_random_state
from sklearn.utils import check_array
from sklearn.utils.validation import check_is_fitted
from sklearn.utils.random import sample_without_replacement

from .lof import LOF
from .base import BaseDetector
from .base import clone
from .combination import average, maximization
from ..utils.utility import check_parameter

MAX_INT = np.iinfo(np.int32).max


def _generate_indices(random_state, bootstrap, n_population, n_samples):
    """
    Draw randomly sampled indices.

    See sklearn/ensemble/bagging.py
    """
    # Draw sample indices
    if bootstrap:
        indices = random_state.randint(0, n_population, n_samples)
    else:
        indices = sample_without_replacement(n_population, n_samples,
                                             random_state=random_state)

    return indices


def _generate_bagging_indices(random_state, bootstrap_features, n_features,
                              min_features, max_features):
    """
    Randomly draw feature indices.

    Modified from sklearn/ensemble/bagging.py
    """
    # Get valid random state
    random_state = check_random_state(random_state)

    # decide number of features to draw
    random_n_features = random_state.randint(min_features, max_features)

    # Draw indices
    feature_indices = _generate_indices(random_state, bootstrap_features,
                                        n_features, random_n_features)

    return feature_indices


def _set_random_states(estimator, random_state=None):
    """Sets fixed random_state parameters for an estimator

    sklearn/base.py

    Finds all parameters ending ``random_state`` and sets them to integers
    derived from ``random_state``.

    Parameters
    ----------

    estimator : estimator supporting get/set_params
        Estimator with potential randomness managed by random_state
        parameters.

    random_state : int, RandomState instance or None, optional (default=None)
        If int, random_state is the seed used by the random number generator;
        If RandomState instance, random_state is the random number generator;
        If None, the random number generator is the RandomState instance used
        by `np.random`.

    Notes
    -----
    This does not necessarily set *all* ``random_state`` attributes that
    control an estimator's randomness, only those accessible through
    ``estimator.get_params()``.  ``random_state``s not controlled include
    those belonging to:

        * cross-validation splitters
        * ``scipy.stats`` rvs
    """
    random_state = check_random_state(random_state)
    to_set = {}
    for key in sorted(estimator.get_params(deep=True)):
        if key == 'random_state' or key.endswith('__random_state'):
            to_set[key] = random_state.randint(MAX_INT)

    if to_set:
        estimator.set_params(**to_set)


class FeatureBagging(BaseDetector):
    """
    A feature bagging detector is a meta estimator that fits a number of
    base detectors on various sub-samples of the dataset and use averaging
    or other combination methods to improve the predictive accuracy and
    control over-fitting.

    The sub-sample size is always the same as the original input sample size
    but the features are randomly sampled from half of the features to all
    features.

    .. [1] Lazarevic, A. and Kumar, V., 2005, August. Feature bagging for
           outlier detection. In KDD '05. 2005.

    """

    def __init__(self, base_estimator=LOF(), n_estimators=10,
                 contamination=0.1, max_features=1.0, bootstrap_features=False,
                 random_state=None, combination='average',
                 estimator_params={}):
        super(FeatureBagging, self).__init__(contamination=contamination)
        self.base_estimator = base_estimator
        self.n_estimators = n_estimators
        self.max_features = max_features
        self.bootstrap_features = bootstrap_features
        self.combination = combination
        self.random_state = random_state
        self.estimator_params = estimator_params

    def fit(self, X, y=None):
        random_state = check_random_state(self.random_state)

        X = check_array(X)
        self.n_samples_, self.n_features_ = X.shape[0], X.shape[1]

        self._set_n_classes(y)

        # expect at least 2 features, does not make sense if only have
        # 1 feature
        check_parameter(self.n_features_, low=2, include_left=True,
                        param_name='n_features')

        # check parameters
        self._validate_estimator()

        # use at least half of the features
        self.min_features_ = int(0.5 * self.n_features_)

        # Validate max_features
        if isinstance(self.max_features, (numbers.Integral, np.integer)):
            self.max_features_ = self.max_features
        else:  # float
            self.max_features_ = int(self.max_features * self.n_features_)

        # min_features and max_features could equal
        check_parameter(self.max_features_, low=self.min_features_,
                        param_name='max_features', high=self.n_features_,
                        include_left=True, include_right=True)

        self.estimators_ = []
        self.estimators_features_ = []

        n_more_estimators = self.n_estimators - len(self.estimators_)

        if n_more_estimators < 0:
            raise ValueError('n_estimators=%d must be larger or equal to '
                             'len(estimators_)=%d when warm_start==True'
                             % (self.n_estimators, len(self.estimators_)))

        seeds = random_state.randint(MAX_INT, size=n_more_estimators)
        self._seeds = seeds

        for i in range(self.n_estimators):
            random_state = np.random.RandomState(seeds[i])

            # max_features is incremented by one since random
            # function is [min_features, max_features)
            features = _generate_bagging_indices(random_state,
                                                 self.bootstrap_features,
                                                 self.n_features_,
                                                 self.min_features_,
                                                 self.max_features_ + 1)
            # initialize and append estimators
            estimator = self._make_estimator(append=False,
                                             random_state=random_state)
            estimator.fit(X[:, features])

            self.estimators_.append(estimator)
            self.estimators_features_.append(features)

        # decision score matrix from all estimators
        all_decision_scores = self._get_decision_scores()

        if self.combination == 'average':
            self.decision_scores_ = average(all_decision_scores)
        else:
            self.decision_scores_ = maximization(all_decision_scores)

        self._process_decision_scores()

        return self

    def decision_function(self, X):
        """
        sklearn/ensemble/bagging.predict_proba()
        :param X:
        :return:
        """
        check_is_fitted(self, ['estimators_', 'estimators_features_',
                               'decision_scores_', 'threshold_', 'labels_'])
        X = check_array(X)

        if self.n_features_ != X.shape[1]:
            raise ValueError("Number of features of the model must "
                             "match the input. Model n_features is {0} and "
                             "input n_features is {1}."
                             "".format(self.n_features_, X.shape[1]))
        all_pred_scores = self._predict_decision_scores(X)

        if self.combination == 'average':
            pred_scores = average(all_pred_scores)
        else:
            pred_scores = maximization(all_pred_scores)

        return pred_scores

    def _predict_decision_scores(self, X):
        all_pred_scores = np.zeros([X.shape[0], self.n_estimators])
        for i in range(self.n_estimators):
            features = self.estimators_features_[i]
            all_pred_scores[:, i] = self.estimators_[i].decision_function(
                X[:, features])
        return all_pred_scores

    def _get_decision_scores(self):
        all_decision_scores = np.zeros([self.n_samples_, self.n_estimators])
        for i in range(self.n_estimators):
            all_decision_scores[:, i] = self.estimators_[i].decision_scores_
        return all_decision_scores

    def _validate_estimator(self, default=None):
        """Check the estimator and the n_estimator attribute, set the
        `base_estimator_` attribute."""
        if not isinstance(self.n_estimators, (numbers.Integral, np.integer)):
            raise ValueError("n_estimators must be an integer, "
                             "got {0}.".format(type(self.n_estimators)))

        if self.n_estimators <= 0:
            raise ValueError("n_estimators must be greater than zero, "
                             "got {0}.".format(self.n_estimators))

        if self.base_estimator is not None:
            self.base_estimator_ = self.base_estimator
        else:
            self.base_estimator_ = default

        if self.base_estimator_ is None:
            raise ValueError("base_estimator cannot be None")

    def _make_estimator(self, append=True, random_state=None):
        """Make and configure a copy of the `base_estimator_` attribute.

        sklearn/base.py

        Warning: This method should be used to properly instantiate new
        sub-estimators.
        """
        estimator = clone(self.base_estimator_)

        # TODO: this is not right, shoult automatic pass parameters of
        # feature bagging to the base_estimators_
        estimator.set_params(**self.estimator_params)

        if random_state is not None:
            _set_random_states(estimator, random_state)

        if append:
            self.estimators_.append(estimator)

        return estimator

    def __len__(self):
        """Returns the number of estimators in the ensemble."""
        return len(self.estimators_)

    def __getitem__(self, index):
        """Returns the index'th estimator in the ensemble."""
        return self.estimators_[index]

    def __iter__(self):
        """Returns iterator over estimators in the ensemble."""
        return iter(self.estimators_)
