import logging
from src import settings
from typing import Dict, Any
from src.plugins.interface import PluginInterface
from src.databases.interface import DatabaseInterface
from src.exceptions import PluginError, DatabaseUnsupportedError


logger = logging.getLogger(__name__)


class PsbtValidation(PluginInterface):

    async def init(self, *args, **kwargs):
        await self._create_db_instance(settings.DB_TYPE)

    async def process_request(self, data: Dict[str, Any]) -> bool:
        """Entry point - Validates the PSBT exists in the database"""
        try:
            psbt = data.get("extraParameters", {}).get("psbt")
            if not psbt:
                raise PluginError(
                    "Missing extraParameters.psbt in the transaction object"
                )

            sourceType = data.get("sourceType")
            if sourceType not in ["VAULT"]:
                raise PluginError("Invalid source type. Must be 'VAULT_ACCOUNT'")

            assetId = data.get("asset")
            if assetId not in ["BTC", "BTC_TEST"]:
                raise PluginError("Invalid assetId. Must be 'BTC' or 'BTC_TEST'")

            vaultAccountId = data.get("sourceId")
            if not assetId or not vaultAccountId:
                raise PluginError(
                    "Missing assetId or vaultAccountId in the transaction object"
                )

            signatureRequests = (
                data.get("extraParameters", {})
                .get("rawMessageData", {})
                .get("messages", False)
            )
            if not signatureRequests:
                raise PluginError(
                    "Missing extraParameters.rawMessageData.messages in the transaction object"
                )

            result = await self._validate_psbt(psbt, vaultAccountId, signatureRequests)
            logger.info(f"Approval result from PSBT Validation Plugin is: {result}")
            return result
        except Exception as e:
            raise PluginError(f"Unexpected error in PSBT Validation plugin: {e}")

    async def _validate_psbt(
        self, psbt: str, vaultAccountId: str, signatureRequests: list[dict[str, Any]]
    ) -> bool:
        """Checks the database for the existence of a PSBT."""
        logger.info(f"Validating that the PSBT exists in the DB")
        query_result = await self.db.build_query(
            psbt,
            method="find_one",
            db_table=settings.DB_TABLE,
            db_column=settings.DB_COLUMN,
        )
        logger.info(f"Query result is: {query_result}")
        exists = await self.db.execute_query(query_result)
        psbt_exists = bool(exists)
        if not psbt_exists:
            raise PluginError("PSBT does not exist in the DB")

        # Calculate signature hashes from PSBT
        signature_hash_set = self.psbt_to_signature_hashes(psbt)
        logger.info(f"Signature hash set: {signature_hash_set}")

        for request in signatureRequests:
            if request.get("content") not in signature_hash_set:
                raise PluginError(
                    f"Signature hash not found in PSBT: {request.get('content')}"
                )

        return True

    async def _create_db_instance(self, db_type: str) -> DatabaseInterface:
        """Create a DB instance based on the provided DB_TYPE and DB_CLASS_MAP"""
        db_class = settings.DB_CLASS_MAP.get(db_type)
        if db_class is None:
            raise DatabaseUnsupportedError(f"Unsupported database type: {db_type}")
        return await self.set_db_instance(db_class)

    def __repr__(self) -> str:
        return "<PSBT Validation Plugin>"

    def psbt_to_signature_hashes(self, b64_psbt):
        from bitcointx.core.psbt import PartiallySignedTransaction # import here fixes a bug

        psbt = PartiallySignedTransaction.from_base64_or_binary(b64_psbt)
        sighashes = set()

        class SighashGatherer:
            def get_privkey(self, x, y):
                class FakePrivKey:
                    pub = b"00"

                    def sign(self, sighash):
                        sighashes.add(sighash.hex())
                        return b"00"

                return FakePrivKey()

        psbt.sign(SighashGatherer())

        return sighashes
