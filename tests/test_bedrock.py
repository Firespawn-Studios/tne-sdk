"""Tests for BedrockProvider construction and provider_from_profile routing."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class TestBedrockProviderConstruction:
    """Test BedrockProvider init validation and boto3 client setup."""

    def test_missing_boto3_raises_import_error(self):
        """If boto3 is not installed, a clear error message is raised."""
        with patch.dict(sys.modules, {"boto3": None}):
            # Force re-import to trigger the ImportError path
            import importlib
            from tne_sdk.llm import bedrock
            importlib.reload(bedrock)
            with pytest.raises(ImportError, match="boto3 is required"):
                bedrock.BedrockProvider(model_id="test-model")

    def test_empty_model_id_raises(self):
        """model_id is required."""
        mock_boto3 = MagicMock()
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            import importlib
            from tne_sdk.llm import bedrock
            importlib.reload(bedrock)
            with pytest.raises(ValueError, match="model_id is required"):
                bedrock.BedrockProvider(model_id="")

    def test_valid_construction(self):
        """Provider constructs successfully with valid model_id."""
        mock_boto3 = MagicMock()
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            import importlib
            from tne_sdk.llm import bedrock
            importlib.reload(bedrock)
            provider = bedrock.BedrockProvider(
                model_id="mistral.ministral-3-8b-instruct",
                region="us-west-2",
            )
            assert provider.model_id == "mistral.ministral-3-8b-instruct"
            mock_boto3.client.assert_called_once_with(
                "bedrock-runtime", region_name="us-west-2"
            )

    def test_explicit_credentials_forwarded(self):
        """Explicit AWS credentials are passed to boto3.client."""
        mock_boto3 = MagicMock()
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            import importlib
            from tne_sdk.llm import bedrock
            importlib.reload(bedrock)
            bedrock.BedrockProvider(
                model_id="test-model",
                region="eu-west-1",
                access_key="AKIA_TEST",
                secret_key="secret_test",
                session_token="token_test",
            )
            mock_boto3.client.assert_called_once_with(
                "bedrock-runtime",
                region_name="eu-west-1",
                aws_access_key_id="AKIA_TEST",
                aws_secret_access_key="secret_test",
                aws_session_token="token_test",
            )

    def test_default_credential_chain_when_no_keys(self):
        """Without explicit keys, only region is passed (boto3 uses its chain)."""
        mock_boto3 = MagicMock()
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            import importlib
            from tne_sdk.llm import bedrock
            importlib.reload(bedrock)
            bedrock.BedrockProvider(model_id="test-model")
            mock_boto3.client.assert_called_once_with(
                "bedrock-runtime", region_name="us-east-1"
            )


class TestBedrockChatCompletion:
    """Test the chat_completion method with mocked boto3."""

    def _make_provider(self):
        mock_boto3 = MagicMock()
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            import importlib
            from tne_sdk.llm import bedrock
            importlib.reload(bedrock)
            provider = bedrock.BedrockProvider(model_id="test-model")
        return provider

    def test_system_message_separated(self):
        """System messages go to the system param, not messages."""
        provider = self._make_provider()
        from tne_sdk.llm.providers import Message

        # Mock the converse response
        provider._client.converse.return_value = {
            "output": {
                "message": {
                    "content": [{"text": "Hello from Bedrock"}]
                }
            }
        }

        result = provider.chat_completion(
            messages=[
                Message(role="system", content="You are helpful."),
                Message(role="user", content="Hi there."),
            ],
            temperature=0.5,
        )

        assert result == "Hello from Bedrock"

        # Verify the converse call structure
        call_kwargs = provider._client.converse.call_args[1]
        assert call_kwargs["system"] == [{"text": "You are helpful."}]
        assert call_kwargs["messages"] == [
            {"role": "user", "content": [{"text": "Hi there."}]}
        ]
        assert call_kwargs["inferenceConfig"]["temperature"] == 0.5

    def test_no_system_message(self):
        """When there's no system message, system param is omitted."""
        provider = self._make_provider()
        from tne_sdk.llm.providers import Message

        provider._client.converse.return_value = {
            "output": {"message": {"content": [{"text": "response"}]}}
        }

        provider.chat_completion(
            messages=[Message(role="user", content="Hello")],
        )

        call_kwargs = provider._client.converse.call_args[1]
        assert "system" not in call_kwargs

    def test_model_override(self):
        """Passing model= overrides the default model_id."""
        provider = self._make_provider()
        from tne_sdk.llm.providers import Message

        provider._client.converse.return_value = {
            "output": {"message": {"content": [{"text": "ok"}]}}
        }

        provider.chat_completion(
            messages=[Message(role="user", content="test")],
            model="override-model",
        )

        call_kwargs = provider._client.converse.call_args[1]
        assert call_kwargs["modelId"] == "override-model"

    def test_reasoning_fallback(self):
        """Falls back to reasoning content when no text block is present."""
        provider = self._make_provider()
        from tne_sdk.llm.providers import Message

        provider._client.converse.return_value = {
            "output": {
                "message": {
                    "content": [{
                        "reasoningContent": {
                            "reasoningText": {
                                "text": "reasoning output here"
                            }
                        }
                    }]
                }
            }
        }

        result = provider.chat_completion(
            messages=[Message(role="user", content="think hard")],
        )
        assert result == "reasoning output here"

    def test_empty_content_returns_empty_string(self):
        """Returns empty string when response has no extractable content."""
        provider = self._make_provider()
        from tne_sdk.llm.providers import Message

        provider._client.converse.return_value = {
            "output": {"message": {"content": []}}
        }

        result = provider.chat_completion(
            messages=[Message(role="user", content="test")],
        )
        assert result == ""

    def test_unsupported_kwargs_stripped(self):
        """SDK-specific kwargs that Converse doesn't understand are stripped."""
        provider = self._make_provider()
        from tne_sdk.llm.providers import Message

        provider._client.converse.return_value = {
            "output": {"message": {"content": [{"text": "ok"}]}}
        }

        # These should not cause errors — they're silently stripped
        provider.chat_completion(
            messages=[Message(role="user", content="test")],
            chat_template_kwargs={"enable_thinking": True},
            presence_penalty=1.5,
            top_k=20,
            frequency_penalty=0.5,
        )

        # Verify none of the stripped params made it into the call
        call_kwargs = provider._client.converse.call_args[1]
        assert "chat_template_kwargs" not in call_kwargs
        assert "presence_penalty" not in call_kwargs


class TestProviderFromProfileBedrock:
    """Test that provider_from_profile routes bedrock:// URLs correctly."""

    def test_bedrock_url_returns_bedrock_provider(self):
        mock_boto3 = MagicMock()
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            import importlib
            from tne_sdk.llm import bedrock
            importlib.reload(bedrock)
            from tne_sdk.llm.providers import provider_from_profile

            profile = {
                "llm_url": "bedrock://us-west-2",
                "model": "mistral.ministral-3-8b-instruct",
            }
            p = provider_from_profile(profile)
            assert isinstance(p, bedrock.BedrockProvider)
            assert p.model_id == "mistral.ministral-3-8b-instruct"

    def test_bedrock_url_default_region(self):
        mock_boto3 = MagicMock()
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            import importlib
            from tne_sdk.llm import bedrock
            importlib.reload(bedrock)
            from tne_sdk.llm.providers import provider_from_profile

            profile = {
                "llm_url": "bedrock://",
                "model": "test-model",
            }
            p = provider_from_profile(profile)
            assert isinstance(p, bedrock.BedrockProvider)
            # Should default to us-east-1
            mock_boto3.client.assert_called_with(
                "bedrock-runtime", region_name="us-east-1"
            )

    def test_bedrock_url_case_insensitive(self):
        mock_boto3 = MagicMock()
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            import importlib
            from tne_sdk.llm import bedrock
            importlib.reload(bedrock)
            from tne_sdk.llm.providers import provider_from_profile

            profile = {
                "llm_url": "BEDROCK://EU-WEST-1",
                "model": "test-model",
            }
            p = provider_from_profile(profile)
            assert isinstance(p, bedrock.BedrockProvider)
