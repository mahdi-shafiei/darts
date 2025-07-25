"""
Linear Regression model
-----------------------

A forecasting model using a linear regression of some of the target series' lags, as well as optionally some
covariate series lags in order to obtain a forecast.
"""

from collections.abc import Sequence
from typing import Optional, Union

from scipy.optimize import linprog
from sklearn.linear_model import LinearRegression, PoissonRegressor, QuantileRegressor

from darts import TimeSeries
from darts.logging import get_logger
from darts.models.forecasting.sklearn_model import (
    FUTURE_LAGS_TYPE,
    LAGS_TYPE,
    SKLearnModel,
    _QuantileModelContainer,
)
from darts.utils.likelihood_models.base import LikelihoodType
from darts.utils.likelihood_models.sklearn import (
    QuantileRegression,
    _get_likelihood,
)

logger = get_logger(__name__)


class LinearRegressionModel(SKLearnModel):
    def __init__(
        self,
        lags: Optional[LAGS_TYPE] = None,
        lags_past_covariates: Optional[LAGS_TYPE] = None,
        lags_future_covariates: Optional[FUTURE_LAGS_TYPE] = None,
        output_chunk_length: int = 1,
        output_chunk_shift: int = 0,
        add_encoders: Optional[dict] = None,
        likelihood: Optional[str] = None,
        quantiles: Optional[list[float]] = None,
        random_state: Optional[int] = None,
        multi_models: Optional[bool] = True,
        use_static_covariates: bool = True,
        **kwargs,
    ):
        """Linear regression model.

        Parameters
        ----------
        lags
            Lagged target `series` values used to predict the next time step/s.
            If an integer, must be > 0. Uses the last `n=lags` past lags; e.g. `(-1, -2, ..., -lags)`, where `0`
            corresponds the first predicted time step of each sample. If `output_chunk_shift > 0`, then
            lag `-1` translates to `-1 - output_chunk_shift` steps before the first prediction step.
            If a list of integers, each value must be < 0. Uses only the specified values as lags.
            If a dictionary, the keys correspond to the `series` component names (of the first series when
            using multiple series) and the values correspond to the component lags (integer or list of integers). The
            key 'default_lags' can be used to provide default lags for un-specified components. Raises and error if some
            components are missing and the 'default_lags' key is not provided.
        lags_past_covariates
            Lagged `past_covariates` values used to predict the next time step/s.
            If an integer, must be > 0. Uses the last `n=lags_past_covariates` past lags; e.g. `(-1, -2, ..., -lags)`,
            where `0` corresponds to the first predicted time step of each sample. If `output_chunk_shift > 0`, then
            lag `-1` translates to `-1 - output_chunk_shift` steps before the first prediction step.
            If a list of integers, each value must be < 0. Uses only the specified values as lags.
            If a dictionary, the keys correspond to the `past_covariates` component names (of the first series when
            using multiple series) and the values correspond to the component lags (integer or list of integers). The
            key 'default_lags' can be used to provide default lags for un-specified components. Raises and error if some
            components are missing and the 'default_lags' key is not provided.
        lags_future_covariates
            Lagged `future_covariates` values used to predict the next time step/s. The lags are always relative to the
            first step in the output chunk, even when `output_chunk_shift > 0`.
            If a tuple of `(past, future)`, both values must be > 0. Uses the last `n=past` past lags and `n=future`
            future lags; e.g. `(-past, -(past - 1), ..., -1, 0, 1, .... future - 1)`, where `0` corresponds the first
            predicted time step of each sample. If `output_chunk_shift > 0`, the position of negative lags differ from
            those of `lags` and `lags_past_covariates`. In this case a future lag `-5` would point at the same
            step as a target lag of `-5 + output_chunk_shift`.
            If a list of integers, uses only the specified values as lags.
            If a dictionary, the keys correspond to the `future_covariates` component names (of the first series when
            using multiple series) and the values correspond to the component lags (tuple or list of integers). The key
            'default_lags' can be used to provide default lags for un-specified components. Raises and error if some
            components are missing and the 'default_lags' key is not provided.
        output_chunk_length
            Number of time steps predicted at once (per chunk) by the internal model. It is not the same as forecast
            horizon `n` used in `predict()`, which is the desired number of prediction points generated using a
            one-shot- or autoregressive forecast. Setting `n <= output_chunk_length` prevents auto-regression. This is
            useful when the covariates don't extend far enough into the future, or to prohibit the model from using
            future values of past and / or future covariates for prediction (depending on the model's covariate
            support).
        output_chunk_shift
            Optionally, the number of steps to shift the start of the output chunk into the future (relative to the
            input chunk end). This will create a gap between the input (history of target and past covariates) and
            output. If the model supports `future_covariates`, the `lags_future_covariates` are relative to the first
            step in the shifted output chunk. Predictions will start `output_chunk_shift` steps after the end of the
            target `series`. If `output_chunk_shift` is set, the model cannot generate autoregressive predictions
            (`n > output_chunk_length`).
        add_encoders
            A large number of past and future covariates can be automatically generated with `add_encoders`.
            This can be done by adding multiple pre-defined index encoders and/or custom user-made functions that
            will be used as index encoders. Additionally, a transformer such as Darts' :class:`Scaler` can be added to
            transform the generated covariates. This happens all under one hood and only needs to be specified at
            model creation.
            Read :meth:`SequentialEncoder <darts.dataprocessing.encoders.SequentialEncoder>` to find out more about
            ``add_encoders``. Default: ``None``. An example showing some of ``add_encoders`` features:

            .. highlight:: python
            .. code-block:: python

                def encode_year(idx):
                    return (idx.year - 1950) / 50

                add_encoders={
                    'cyclic': {'future': ['month']},
                    'datetime_attribute': {'future': ['hour', 'dayofweek']},
                    'position': {'past': ['relative'], 'future': ['relative']},
                    'custom': {'past': [encode_year]},
                    'transformer': Scaler(),
                    'tz': 'CET'
                }
            ..
        likelihood
            Can be set to `quantile` or `poisson`. If set, the model will be probabilistic, allowing sampling at
            prediction time. If set to `quantile`, the `sklearn.linear_model.QuantileRegressor` is used. Similarly, if
            set to `poisson`, the `sklearn.linear_model.PoissonRegressor` is used.
        quantiles
            Fit the model to these quantiles if the `likelihood` is set to `quantile`.
        random_state
            Controls the randomness for reproducible forecasting.
        multi_models
            If True, a separate model will be trained for each future lag to predict. If False, a single model
            is trained to predict all the steps in 'output_chunk_length' (features lags are shifted back by
            `output_chunk_length - n` for each step `n`). Default: True.
        use_static_covariates
            Whether the model should use static covariate information in case the input `series` passed to ``fit()``
            contain static covariates. If ``True``, and static covariates are available at fitting time, will enforce
            that all target `series` have the same static covariate dimensionality in ``fit()`` and ``predict()``.
        **kwargs
            Additional keyword arguments passed to `sklearn.linear_model.LinearRegression` (by default), to
            `sklearn.linear_model.PoissonRegressor` (if `likelihood="poisson"`), or to
            `sklearn.linear_model.QuantileRegressor` (if `likelihood="quantile"`).

        Examples
        --------
        Deterministic forecasting, using past/future covariates (optional)

        >>> from darts.datasets import WeatherDataset
        >>> from darts.models import LinearRegressionModel
        >>> series = WeatherDataset().load()
        >>> # predicting atmospheric pressure
        >>> target = series['p (mbar)'][:100]
        >>> # optionally, use past observed rainfall (pretending to be unknown beyond index 100)
        >>> past_cov = series['rain (mm)'][:100]
        >>> # optionally, use future temperatures (pretending this component is a forecast)
        >>> future_cov = series['T (degC)'][:106]
        >>> # predict 6 pressure values using the 12 past values of pressure and rainfall, as well as the 6 temperature
        >>> # values corresponding to the forecasted period
        >>> model = LinearRegressionModel(
        >>>     lags=12,
        >>>     lags_past_covariates=12,
        >>>     lags_future_covariates=[0,1,2,3,4,5],
        >>>     output_chunk_length=6,
        >>> )
        >>> model.fit(target, past_covariates=past_cov, future_covariates=future_cov)
        >>> pred = model.predict(6)
        >>> pred.values()
        array([[1005.72085839],
               [1005.6548696 ],
               [1005.65403772],
               [1005.6846175 ],
               [1005.75753605],
               [1005.81830675]])
        """
        self.kwargs = kwargs

        self._likelihood = _get_likelihood(
            likelihood=likelihood,
            n_outputs=output_chunk_length if multi_models else 1,
            quantiles=quantiles,
            available_likelihoods=[LikelihoodType.Quantile, LikelihoodType.Poisson],
        )

        if likelihood == LikelihoodType.Poisson.value:
            model = PoissonRegressor(**kwargs)
        elif likelihood == LikelihoodType.Quantile.value:
            model = QuantileRegressor(**kwargs)
            self._model_container = _QuantileModelContainer()
        else:  # likelihood is None
            model = LinearRegression(**kwargs)

        super().__init__(
            lags=lags,
            lags_past_covariates=lags_past_covariates,
            lags_future_covariates=lags_future_covariates,
            output_chunk_length=output_chunk_length,
            output_chunk_shift=output_chunk_shift,
            add_encoders=add_encoders,
            model=model,
            multi_models=multi_models,
            use_static_covariates=use_static_covariates,
            random_state=random_state,
        )

    def fit(
        self,
        series: Union[TimeSeries, Sequence[TimeSeries]],
        past_covariates: Optional[Union[TimeSeries, Sequence[TimeSeries]]] = None,
        future_covariates: Optional[Union[TimeSeries, Sequence[TimeSeries]]] = None,
        max_samples_per_ts: Optional[int] = None,
        n_jobs_multioutput_wrapper: Optional[int] = None,
        sample_weight: Optional[Union[TimeSeries, Sequence[TimeSeries], str]] = None,
        **kwargs,
    ):
        likelihood = self.likelihood
        if isinstance(likelihood, QuantileRegression):
            # set solver for linear program
            if "solver" not in self.kwargs:
                # set default fast solver
                self.kwargs["solver"] = "highs"

            # test solver availability with dummy problem
            c = [1]
            try:
                linprog(c=c, method=self.kwargs["solver"])
            except ValueError as ve:
                logger.warning(
                    f"{ve}. Upgrading scipy enables significantly faster solvers"
                )
                # set solver to slow legacy
                self.kwargs["solver"] = "interior-point"

            # empty model container in case of multiple calls to fit, e.g. when backtesting
            self._model_container.clear()

            for quantile in likelihood.quantiles:
                self.kwargs["quantile"] = quantile
                # assign the Quantile regressor to self.model to leverage existing logic
                self.model = QuantileRegressor(**self.kwargs)
                super().fit(
                    series=series,
                    past_covariates=past_covariates,
                    future_covariates=future_covariates,
                    max_samples_per_ts=max_samples_per_ts,
                    n_jobs_multioutput_wrapper=n_jobs_multioutput_wrapper,
                    sample_weight=sample_weight,
                    **kwargs,
                )
                # store the trained model in the container as it might have been wrapped by MultiOutputRegressor
                self._model_container[quantile] = self.model

            # replace the last trained QuantileRegressor with the dictionary of Regressors.
            self.model = self._model_container

            return self

        else:
            super().fit(
                series=series,
                past_covariates=past_covariates,
                future_covariates=future_covariates,
                max_samples_per_ts=max_samples_per_ts,
                n_jobs_multioutput_wrapper=n_jobs_multioutput_wrapper,
                sample_weight=sample_weight,
                **kwargs,
            )

            return self
