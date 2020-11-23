"""
Smart predictor module
"""
from shapash.utils.check import check_model, check_preprocessing, check_preprocessing_options
from shapash.utils.check import check_label_dict, check_mask_params, check_ypred, check_contribution_object
from .smart_state import SmartState
from .multi_decorator import MultiDecorator
import pandas as pd
from shapash.utils.transform import adapt_contributions
from shapash.utils.shap_backend import check_explainer, shap_contributions
from shapash.manipulation.select_lines import keep_right_contributions
from shapash.utils.model import predict_proba
from shapash.utils.transform import apply_preprocessing
from shapash.manipulation.filters import hide_contributions
from shapash.manipulation.filters import cap_contributions
from shapash.manipulation.filters import sign_contributions
from shapash.manipulation.filters import cutoff_contributions
from shapash.manipulation.filters import combine_masks
from shapash.manipulation.mask import init_mask
from shapash.manipulation.mask import compute_masked_contributions
from shapash.manipulation.summarize import summarize
from shapash.decomposition.contributions import rank_contributions, assign_contributions

class SmartPredictor :
    """
    The SmartPredictor class is an object which inherits from the
    SmartExplainer class.

    Thanks to an explainer, It allows the Data Scientists to perform operations
    to make results more understandable on new datasets with the same structure as
    the one used to build the explainer.

    As a lighter layer of the SmartExplainer, SmartPredictor is designed to perform
    the essential operations to make new results understandable :
    linking to preprocessing and postprocessing already used, models contributions,
    predictions, local epxlainability.

    This class allows the user to automatically summarize the results of his model
    on new datasets (prediction, preprocessing and postprocessing linking,
    explainability).

    The SmartPredictor has several methods described below.

    The SmartPredictor Attributes :

    features_dict: dict
        Dictionary mapping technical feature names to domain names.
    model: model object
        model used to check the different values of target estimate predict_proba
    explainer : explainer object
            explainer must be a shap object
    columns_dict: dict
        Dictionary mapping integer column number (in the same order of the trained dataset) to technical feature names.
    features_types: dict
        Dictionnary mapping features with the right types needed.
    label_dict: dict (optional)
        Dictionary mapping integer labels to domain names (classification - target values).
    preprocessing: category_encoders, ColumnTransformer, list or dict (optional)
        The processing apply to the original data.
    postprocessing: dict (optional)
        Dictionnary of postprocessing modifications to apply in x_pred dataframe.
    _case: string
        String that informs if the model used is for classification or regression problem.
    _classes: list, None
        List of labels if the model used is for classification problem, None otherwise.
    mask_params: dict (optional)
        Dictionnary allowing the user to define a apply a filter to summarize the local explainability.

    How to declare a new SmartPredictor object?

    Example
    --------
    >>> predictor = SmartPredictor(features_dict,
                                    model,
                                    explainer,
                                    columns_dict,
                                    features_types,
                                    label_dict,
                                    preprocessing,
                                    postprocessing
                                    )

    or predictor = xpl.to_smartpredictor()

    xpl, explainer: object
        SmartExplainer instance to point to.
    """

    def __init__(self, features_dict, model,
                 columns_dict, explainer, features_types,
                 label_dict=None, preprocessing=None,
                 postprocessing=None,
                 mask_params = {"features_to_hide": None,
                                "threshold": None,
                                "positive": None,
                                "max_contrib": None
                                }
                 ):

        params_dict = [features_dict, features_types, label_dict, columns_dict, postprocessing]

        for params in params_dict:
            if params is not None and isinstance(params, dict) == False:
                raise ValueError(
                    """
                    {0} must be a dict.
                    """.format(str(params))
                )

        self.model = model
        self._case, self._classes = self.check_model()
        self.explainer = self.check_explainer(explainer)
        check_preprocessing_options(preprocessing)
        self.preprocessing = preprocessing
        self.check_preprocessing()
        self.features_dict = features_dict
        self.features_types = features_types
        self.label_dict = label_dict
        self.check_label_dict()
        self.postprocessing = postprocessing
        self.columns_dict = columns_dict
        self.mask_params = mask_params
        self.check_mask_params()

    def check_model(self):
        """
        Check if model has a predict_proba method is a one column dataframe of integer or float
        and if y_pred index matches x_pred index

        Returns
        -------
        string:
            'regression' or 'classification' according to the attributes of the model
        """
        _case, _classes = check_model(self.model)
        return _case, _classes

    def check_preprocessing(self):
        """
        Check that all transformation of the preprocessing are supported.
        """
        return check_preprocessing(self.preprocessing)

    def check_label_dict(self):
        """
        Check if label_dict and model _classes match
        """
        if self._case != "regression":
            return check_label_dict(self.label_dict, self._case, self._classes)

    def check_mask_params(self):
        """
        Check if mask_params given respect the expected format.
        """
        return check_mask_params(self.mask_params)

    def add_input(self, x=None, ypred=None, contributions=None):
        """
        The add_input method is the first step to add a dataset for prediction and explainability. It checks
        the structure of the dataset, the prediction and the contribution if specified. It applies the preprocessing
        specified in the initialisation and reorder the features with the order used by the model.

        It's possible to not specified one parameter if it has already been defined before.
        For example, if the user want to specified a ypred without reinitialize the dataset x already defined before.
        If the user declare a new input x, all the parameters stored will be cleaned.

        Example
        --------
        >>> predictor.add_input(x=xtest_df)
        >>> predictor.add_input(ypred=ytest_df)

        Parameters
        ----------
        x: dict, pandas.DataFrame (optional)
            Raw dataset used by the model to perform the prediction (not preprocessed).
        ypred: pandas.DataFrame (optional)
            User-specified prediction values.
        contributions: pandas.DataFrame (regression) or list (classification) (optional)
            local contributions aggregated if the preprocessing part requires it (e.g. one-hot encoding).

        """
        if x is not None:
            x = self.check_dataset_features(self.check_dataset_type(x))
            self.data = self.clean_data(x)
            try :
                self.data["x_preprocessed"] = self.apply_preprocessing()
            except BaseException :
                raise ValueError(
                    """
                    Preprocessing has failed. The preprocessing specified or the dataset doesn't match.
                    """
                )
        else:
            if not hasattr(self,"data"):
                raise ValueError ("No dataset x specified.")

        if ypred is not None:
            self.data["ypred"] = self.check_ypred(ypred)

        if contributions is not None:
            self.data["ypred"], self.data["contributions"] = self.compute_contributions(contributions=contributions)
        else:
            self.data["ypred"], self.data["contributions"]  = self.compute_contributions()

    def check_dataset_type(self, x=None):
        """
        Check if dataset x given respect the expected format.

        Parameters
        ----------
        x: dict, pandas.DataFrame (optional)
            Raw dataset used by the model to perform the prediction (not preprocessed).

        Returns
        -------
        x: pandas.DataFrame
            Raw dataset used by the model to perform the prediction (not preprocessed).

        """
        if not (type(x) in [pd.DataFrame, dict]):
            raise ValueError(
                """
                x must be a dict or a pandas.DataFrame.
                """
            )
        else :
            x = self.convert_dict_dataset(x)
        return x

    def convert_dict_dataset(self, x):
        """
        Convert a dict to a dataframe if the dataset specified is a dict.

        Parameters
        ----------
        x: dict
            Raw dataset used by the model to perform the prediction (not preprocessed).

        Returns
        -------
        x: pandas.DataFrame
            Raw dataset used by the model to perform the prediction (not preprocessed).
        """
        if type(x) == dict:
            try:
                if not all(column in self.features_types.keys() for column in x.keys()):
                    raise ValueError("""
                    All features from dataset x must be in the features_types dict initialized.
                    """)
                x = pd.DataFrame.from_dict(x, orient="index").T
                for feature, type_feature in self.features_types.items():
                    x[feature] = x[feature].astype(type_feature)
            except BaseException:
                raise ValueError(
                    """
                    The structure of the given dict x isn't at the right format.
                    """
                )
        return x

    def check_dataset_features(self, x):
        """
        Check if the features of the dataset x has the expected types before using preprocessing and model.

        Parameters
        ----------
        x: pandas.DataFrame (optional)
            Raw dataset used by the model to perform the prediction (not preprocessed).
        """
        assert all(column in self.columns_dict.values() for column in x.columns)
        if not (type(key) == int for key in self.columns_dict.keys()):
            raise ValueError("columns_dict must have only integers keys for features order.")
        features_order = []
        for order in range(min(self.columns_dict.keys()), max(self.columns_dict.keys()) + 1):
            features_order.append(self.columns_dict[order])
        x = x[features_order]

        assert all(column in self.features_types.keys() for column in x.columns)
        if not(str(x[feature].dtypes) == self.features_types[feature] for feature in x.columns):
            raise ValueError("Types of features in x doesn't match with the expected one in features_types.")
        return x

    def check_ypred(self, ypred=None):
        """
        Check that ypred given has the right shape and expected value.

        Parameters
        ----------
        ypred: pandas.DataFrame (optional)
            User-specified prediction values.
        """
        return check_ypred(self.data["x"],ypred)

    def choose_state(self, contributions):
        """
        Select implementation of the smart predictor. Typically check if it is a
        multi-class problem, in which case the implementation should be adapted
        to lists of contributions.

        Parameters
        ----------
        contributions : object
            Local contributions. Could also be a list of local contributions.

        Returns
        -------
        object
            SmartState or SmartMultiState, depending on the nature of the input.
        """
        if isinstance(contributions, list):
            return MultiDecorator(SmartState())
        else:
            return SmartState()

    def adapt_contributions(self, contributions):
        """
        If _case is "classification" and contributions a np.array or pd.DataFrame
        this function transform contributions matrix in a list of 2 contributions
        matrices: Opposite contributions and contributions matrices.

        Parameters
        ----------
        contributions : pandas.DataFrame, np.ndarray or list

        Returns
        -------
            pandas.DataFrame, np.ndarray or list
            contributions object modified
        """
        return adapt_contributions(self._case, contributions)

    def validate_contributions(self, contributions):
        """
        Check len of list if _case is "classification"
        Check contributions object type if _case is "regression"
        Check type of contributions and transform into (list of) pd.Dataframe if necessary

        Parameters
        ----------
        contributions : pandas.DataFrame, np.ndarray or list

        Returns
        -------
            pandas.DataFrame or list
        """
        check_contribution_object(self._case, self._classes, contributions)
        return self.state.validate_contributions(contributions, self.data["x_preprocessed"])

    def check_contributions(self, contributions):
        """
        Check if contributions and prediction set match in terms of shape and index.
        """
        if not self.state.check_contributions(contributions, self.data["x"], features_names=False):
            raise ValueError(
                """
                Prediction set and contributions should have exactly the same number of lines
                and number of columns. the order of the columns must be the same
                Please check x, contributions and preprocessing arguments.
                """
            )

    def clean_data(self, x):
        """
        Clean data stored if x is defined and not None.

        Parameters
        ----------
        x: pandas.DataFrame
            Raw dataset used by the model to perform the prediction (not preprocessed).

        Returns
        -------
            dict of data stored
        """
        return {"x" : x,
                "ypred" : None,
                "contributions" : None,
                "x_preprocessed": None
                }

    def check_explainer(self, explainer):
        """
        Check if explainer class correspond to a shap explainer object
        """
        return check_explainer(explainer)

    def predict_proba(self):
        """
        The predict_proba compute the proba values for each x row defined in add_input

        Returns
        -------
        pandas.DataFrame
            data with all probabilities if there is no ypred data or data with ypred and the associated probability.
        """
        return predict_proba(self.model, self.data["x_preprocessed"], self._classes)

    def compute_contributions(self, contributions=None):
        """
        The compute_contributions compute the contributions associated to data ypred specified.
        Need a data ypred specified in an add_input to display detail_contributions.

        Parameters
        -------
        contributions : object (optional)
            Local contributions, or list of local contributions.

        Returns
        -------
        pandas.DataFrame
            Data with contributions associated to the ypred specified.
        pandas.DataFrame
            ypred data with right probabilities associated.

        """
        if not hasattr(self, "data"):
            raise ValueError("add_input method must be called at least once.")
        if self.data["x"] is None:
            raise ValueError(
                """
                x must be specified in an add_input method to apply detail_contributions.
                """
            )
        if self.data["ypred"] is None:
            self.predict()

        if contributions is None:
            contributions, explainer = shap_contributions(self.model,
                                               self.data["x_preprocessed"],
                                               self.explainer)
        adapt_contrib = self.adapt_contributions(contributions)
        self.state = self.choose_state(adapt_contrib)
        contributions = self.validate_contributions(adapt_contrib)
        contributions = self.apply_preprocessing_for_contributions(contributions,
                                                                   self.preprocessing
                                                                   )
        self.check_contributions(contributions)
        proba_values = self.predict_proba() if self._case == "classification" else None
        y_pred, match_contrib = keep_right_contributions(self.data["ypred"], contributions,
                                 self._case, self._classes,
                                 self.label_dict, proba_values)
        return y_pred, match_contrib

    def detail_contributions(self, contributions=None):
        """
        The detail_contributions compute the contributions associated to data ypred specified.
        Need a data ypred specified in an add_input to display detail_contributions.

        Parameters
        -------
        proba: bool, optional (default: False)
            adding proba in output df
        contributions : object (optional)
            Local contributions, or list of local contributions.

        Returns
        -------
        pandas.DataFrame
            Data with ypred and the associated contributions.
        """
        y_pred, summary = self.compute_contributions(contributions=contributions)
        return pd.concat([y_pred, summary], axis=1)

    def apply_preprocessing_for_contributions(self, contributions, preprocessing=None):
        """
        Reconstruct contributions for original features, taken into account a preprocessing.

        Parameters
        ----------
        contributions : object
            Local contributions, or list of local contributions.
        preprocessing : object
            Encoder taken from scikit-learn or category_encoders

        Returns
        -------
        object
            Reconstructed local contributions in the original space. Can be a list.
        """
        if preprocessing:
            return self.state.inverse_transform_contributions(
                contributions,
                preprocessing
            )
        else:
            return contributions

    def apply_preprocessing(self):
        """
        Apply preprocessing on new dataset input specified.
        """
        return apply_preprocessing(self.data["x"], self.model, self.preprocessing)

    def filter(self):
        """
        The filter method is an important method which allows to summarize the local explainability
        by using the user defined mask_params parameters which correspond to its use case.
        """
        mask = [init_mask(self.summary['contrib_sorted'], True)]
        if self.mask_params["features_to_hide"] is not None:
            mask.append(
                hide_contributions(
                    self.data['var_dict'],
                    features_list=self.check_features_name(self.mask_params["features_to_hide"])
                )
            )
        if self.mask_params["threshold"] is not None:
            mask.append(
                cap_contributions(
                    self.data['contrib_sorted'],
                    threshold=self.mask_params["threshold"]
                )
            )
        if self.mask_params["positive"] is not None:
            mask.append(
                sign_contributions(
                    self.summary['contrib_sorted'],
                    positive=self.mask_params["positive"]
                )
            )
        self.mask = combine_masks(mask)
        if self.mask_params["max_contrib"] is not None:
            self.mask = cutoff_contributions(mask=self.mask, k=self.mask_params["max_contrib"])
        self.masked_contributions = compute_masked_contributions(
            self.summary['contrib_sorted'],
            self.mask
        )

    def summarize(self):
        """
        The summarize method allows to export the summary of local explainability.
        This method proposes a set of parameters to summarize the explainability of each point.
        If the user does not specify any, the summarize method uses the mask_params parameters specified during
        the initialisation of the SmartPredictor.

        In classification case, The summarize method summarizes the explicability which corresponds
        to the predicted values specified by the user (with add_input method) and the proba from
        predict_proba associated to the right predicted values.

        Returns
        -------
        pandas.DataFrame
            - selected explanation of each row for classification case

        Examples
        --------
        >>> summary_df = xpl.summarize()
        >>> summary_df
        	pred	proba	    feature_1	value_1	    contribution_1	feature_2	value_2	    contribution_2
        0	0	    0.756416	Sex	        1.0	        0.322308	    Pclass	    3.0	        0.155069
        1	3	    0.628911	Sex	        2.0	        0.585475	    Pclass	    1.0	        0.370504
        2	0	    0.543308	Sex	        2.0	        -0.486667	    Pclass	    3.0	        0.255072
        """
        # data is needed : add_input() method must be called at least once

        if not hasattr(self, "data"):
            raise ValueError("You have to specify dataset x and y_pred arguments. Please use add_input() method.")

        self.summary = assign_contributions(
            rank_contributions(
                self.data["contributions"],
                self.data["x_preprocessed"]
            )
        )
        # Apply filter method with mask_params attributes parameters
        self.filter()

        # Summarize information
        self.data['summary'] = summarize(self.summary['contrib_sorted'],
                                         self.summary['var_dict'],
                                         self.summary['x_sorted'],
                                         self.mask,
                                         self.columns_dict,
                                         self.features_dict)

        # Matching with y_pred
        return pd.concat([self.data["ypred"], self.data['summary']], axis=1)

    def modify_mask(
            self,
            features_to_hide=None,
            threshold=None,
            positive=None,
            max_contrib=None
    ):
        """
        Allow the users to modify the mask_params values.
        Each parameter is optional, modify_mask method modifies only the values specified in parameters.

        Parameters
        ----------
        features_to_hide : list, optional (default: None)
            List of strings, containing features to hide.
        threshold : float, optional (default: None)
            Absolute threshold below which any contribution is hidden.
        positive: bool, optional (default: None)
            If True, hide negative values. False, hide positive values
            If None, hide nothing.
        max_contrib : int, optional (default: None)
            Maximum number of contributions to show.
        """
        Attributes = {"features_to_hide": features_to_hide,
                      "threshold": threshold,
                      "positive": positive,
                      "max_contrib": max_contrib}
        for label, attribute in Attributes.items() :
            if attribute is not None:
                self.mask_params[label] = attribute

    def predict(self):
        """
        The predict compute the predicted values for each x row defined in add_input

        Returns
        -------
        pandas.DataFrame
            data with predicted values for each x row.
        """
        if not hasattr(self, "data"):
            raise ValueError("add_input method must be called at least once.")
        if self.data["x_preprocessed"] is None:
            raise ValueError(
                """
                x must be specified in an add_input method to apply predict.
                """
            )
        if hasattr(self.model, 'predict'):
            self.data["ypred"] = pd.DataFrame(
                self.model.predict(self.data["x_preprocessed"]),
                columns=['ypred'],
                index=self.data["x_preprocessed"].index)
        else:
            raise ValueError("model has no predict method")

        return self.data["ypred"]


