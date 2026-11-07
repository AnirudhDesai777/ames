"""SmallWebRTC audio echo; HTTP signaling is added in S1b."""

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

from server.stubs import model_stages


def echo_pipeline() -> Pipeline:
    return Pipeline(model_stages())


def echo_connection() -> SmallWebRTCConnection:
    return SmallWebRTCConnection(ice_servers=[])


async def run_echo(connection: SmallWebRTCConnection):
    try:
        transport = SmallWebRTCTransport(
            connection,
            TransportParams(audio_in_enabled=True, audio_out_enabled=True),
        )
        worker = PipelineWorker(
            Pipeline([transport.input(), echo_pipeline(), transport.output()]),
            params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=16000),
            enable_rtvi=False,
        )
        runner = WorkerRunner(handle_sigint=False)

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            await runner.cancel()

        await runner.add_workers(worker)
        await runner.run()
    finally:
        await connection.disconnect()
