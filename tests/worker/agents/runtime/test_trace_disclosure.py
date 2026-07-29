from hypothesis import given
from hypothesis import strategies as st

from tracer_agent.worker.agents.runtime.telemetry.disclosure import redact_trace_payload


@st.composite
def secret_keys(draw) -> str:
    parts = [
        "apikey",
        "authorization",
        "callbacktoken",
        "clientsecret",
        "oauth",
        "password",
        "privatekey",
        "scopetoken",
        "secret",
        "token",
        "userid",
    ]
    prefix = draw(st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", max_size=5))
    suffix = draw(st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", max_size=5))
    part = draw(st.sampled_from(parts))
    return f"{prefix}{part}{suffix}"


@st.composite
def non_secret_keys(draw) -> str:
    return draw(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1).filter(
            lambda k: (
                not any(
                    p in k.lower().replace("_", "")
                    for p in [
                        "apikey",
                        "authorization",
                        "callbacktoken",
                        "clientsecret",
                        "oauth",
                        "password",
                        "privatekey",
                        "scopetoken",
                        "secret",
                        "token",
                        "userid",
                    ]
                )
            )
        )
    )


@st.composite
def trace_value(draw, max_depth=3):
    if max_depth <= 0:
        return draw(
            st.one_of(
                st.text(),
                st.integers(),
                st.floats(allow_nan=False, allow_infinity=False),
                st.booleans(),
                st.none(),
            )
        )
    return draw(
        st.one_of(
            st.text(),
            st.integers(),
            st.floats(allow_nan=False, allow_infinity=False),
            st.booleans(),
            st.none(),
            st.lists(trace_value(max_depth=max_depth - 1), max_size=3),
            st.dictionaries(
                keys=st.one_of(secret_keys(), non_secret_keys()),
                values=trace_value(max_depth=max_depth - 1),
                max_size=3,
            ),
        )
    )


def test_지원하지_않는_값을_가리면_unsupported로_적는다():
    assert redact_trace_payload(object()) == "<unsupported>"


@given(trace_value())
def test_추적_payload를_가리면_비밀_값이_하나도_드러나지_않는다(value):
    redacted = redact_trace_payload(value)

    def assert_no_secrets(val, orig):
        if isinstance(orig, dict):
            assert isinstance(val, dict)
            for k, v in orig.items():
                normalized = "".join(c for c in k.lower() if c.isalnum())
                if any(
                    p in normalized
                    for p in [
                        "apikey",
                        "authorization",
                        "callbacktoken",
                        "clientsecret",
                        "oauth",
                        "password",
                        "privatekey",
                        "scopetoken",
                        "secret",
                        "token",
                        "userid",
                    ]
                ):
                    assert val[k] == "<redacted>"
                else:
                    assert_no_secrets(val[k], v)
        elif isinstance(orig, list):
            assert isinstance(val, list)
            for item_val, item_orig in zip(val, orig, strict=True):
                assert_no_secrets(item_val, item_orig)
        else:
            assert val == orig

    assert_no_secrets(redacted, value)
