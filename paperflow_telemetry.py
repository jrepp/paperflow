"""App-owned OTel providers; optional OTLP export never gates ingestion."""
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.metrics import MeterProvider

from paperflow_settings import TelemetrySettings


class Telemetry:
    def __init__(self, settings: TelemetrySettings, *, span_processors=(), metric_readers=()):
        resource = Resource.create({"service.name": settings.service_name})
        self.traces = TracerProvider(resource=resource)
        readers = list(metric_readers)
        for processor in span_processors:
            self.traces.add_span_processor(processor)
        if settings.otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            endpoint = settings.otlp_endpoint.rstrip("/")
            self.traces.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint + "/v1/traces", timeout=2)))
            readers.append(PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=endpoint + "/v1/metrics", timeout=2), export_interval_millis=15000))
        self.metrics = MeterProvider(resource=resource, metric_readers=readers)
        self.tracer = self.traces.get_tracer("paperflow.ingestion")
        meter = self.metrics.get_meter("paperflow.ingestion")
        self.api_duration = meter.create_histogram("paperflow.api.duration", unit="s")
        self.api_bytes = meter.create_histogram("paperflow.api.response.size", unit="By")
        self.api_queries = meter.create_histogram("paperflow.api.db.queries", unit="{query}")
        self.topic_runs = meter.create_counter("paperflow.topics.runs", unit="{run}")
        self.topic_duration = meter.create_histogram("paperflow.topics.duration", unit="s")
        self.topic_candidates = meter.create_counter("paperflow.topics.candidates", unit="{item}")
        self.curation_runs = meter.create_counter("paperflow.curation.runs", unit="{run}")
        self.curation_failures = meter.create_counter("paperflow.curation.failures", unit="{attempt}")
        self.curation_duration = meter.create_histogram("paperflow.curation.duration", unit="s")
        self.curation_tokens = meter.create_counter("paperflow.curation.tokens", unit="{token}")
        self.curation_cached_tokens = meter.create_counter("paperflow.curation.cached_input_tokens", unit="{token}")
        self.curation_spend = meter.create_counter("paperflow.curation.estimated_cost", unit="USD")
        self.curation_unpriced = meter.create_counter("paperflow.curation.unpriced_requests", unit="{request}")
        self.runs = meter.create_counter("paperflow.ingestion.runs", unit="{run}")
        self.duration = meter.create_histogram("paperflow.ingestion.duration", unit="s")
        self.items = meter.create_counter("paperflow.ingestion.items", unit="{item}")
        self.active = meter.create_up_down_counter("paperflow.ingestion.active", unit="{run}")

    def shutdown(self):
        self.traces.shutdown()
        self.metrics.shutdown()
