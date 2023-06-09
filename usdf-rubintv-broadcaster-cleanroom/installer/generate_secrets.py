#!/usr/bin/env python3
import argparse
import base64
import json
import logging
import os
import secrets
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import bcrypt
from cryptography.fernet import Fernet
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from onepasswordconnectsdk.client import new_client_from_environment


class SecretGenerator:
    """A basic secret generator that manages a secrets directory containing
    per-component secret export files from from Vault, as generated by
    read_secrets.sh.

    Parameters
    ----------
    environment : str
        The name of the environment (the environment's domain name).
    regenerate : bool
        If `True`, any secrets that can be generated by the SecretGenerator
        will be regenerated.
    """

    def __init__(self, environment, regenerate):
        self.secrets = defaultdict(dict)
        self.environment = environment
        self.regenerate = regenerate

    def generate(self):
        """Generate secrets for each component based on the `secrets`
        attribute, and regenerating secrets if applicable when the
        `regenerate` attribute is `True`.
        """
        self._pull_secret()
        self._argocd()

        self.input_field("cert-manager", "enabled", "Use cert-manager? (y/n):")
        use_cert_manager = self.secrets["cert-manager"]["enabled"]
        if use_cert_manager == "y":
            self._cert_manager()
        elif use_cert_manager == "n":
            self._ingress_nginx()
        else:
            raise Exception(
                f"Invalid cert manager enabled value {use_cert_manager}"
            )

    def load(self):
        """Load the secrets files for each RSP component from the
        ``secrets`` directory.

        This method parses the JSON files and persists them in the ``secrets``
        attribute, keyed by the component name.
        """
        if Path("secrets").is_dir():
            for f in Path("secrets").iterdir():
                print(f"Loading {f}")
                component = os.path.basename(f)
                self.secrets[component] = json.loads(f.read_text())

    def save(self):
        """For each component, save a secret JSON file into the secrets
        directory.
        """
        os.makedirs("secrets", exist_ok=True)

        for k, v in self.secrets.items():
            with open(f"secrets/{k}", "w") as f:
                f.write(json.dumps(v))

    def input_field(self, component, name, description):
        default = self.secrets[component].get(name, "")
        prompt_string = (
            f"[{component} {name}] ({description}): [current: {default}] "
        )
        input_string = input(prompt_string)

        if input_string:
            self.secrets[component][name] = input_string

    def input_file(self, component, name, description):
        current = self.secrets.get(component, {}).get(name, "")
        print(f"[{component} {name}] ({description})")
        print(f"Current contents:\n{current}")
        prompt_string = "New filename with contents (empty to not change): "
        fname = input(prompt_string)

        print(f"{self.secrets[component]}")
        if fname:
            with open(fname, "r") as f:
                self.secrets[component][name] = f.read()

    @staticmethod
    def _generate_gafaelfawr_token() -> str:
        key = base64.urlsafe_b64encode(os.urandom(16)).decode().rstrip("=")
        secret = base64.urlsafe_b64encode(os.urandom(16)).decode().rstrip("=")
        return f"gt-{key}.{secret}"

    def _get_current(self, component, name):
        if not self._exists(component, name):
            return None

        return self.secrets[component][name]

    def _set(self, component, name, new_value):
        self.secrets[component][name] = new_value

    def _exists(self, component, name):
        return component in self.secrets and name in self.secrets[component]

    def _set_generated(self, component, name, new_value):
        if not self._exists(component, name) or self.regenerate:
            self._set(component, name, new_value)


    def _pull_secret(self):
        self.input_file(
            "pull-secret",
            ".dockerconfigjson",
            ".docker/config.json to pull images",
        )

    def _ingress_nginx(self):
        self.input_file("ingress-nginx", "tls.key", "Certificate private key")
        self.input_file("ingress-nginx", "tls.crt", "Certificate chain")

    def _argocd(self):
        current_pw = self._get_current(
            "installer", "argocd.admin.plaintext_password"
        )

        self.input_field(
            "installer",
            "argocd.admin.plaintext_password",
            "Admin password for ArgoCD?",
        )
        new_pw = self.secrets["installer"]["argocd.admin.plaintext_password"]

        if current_pw != new_pw or self.regenerate:
            h = bcrypt.hashpw(
                new_pw.encode("ascii"), bcrypt.gensalt(rounds=15)
            ).decode("ascii")
            now_time = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

            self._set("argocd", "admin.password", h)
            self._set("argocd", "admin.passwordMtime", now_time)

        self.input_field(
            "argocd",
            "dex.clientSecret",
            "OAuth client secret for ArgoCD (either GitHub or Google)?",
        )

        self._set_generated(
            "argocd", "server.secretkey", secrets.token_hex(16)
        )

class OnePasswordSecretGenerator(SecretGenerator):
    """A secret generator that syncs 1Password secrets into a secrets directory
    containing per-component secret export files from Vault (as generated
    by read_secrets.sh).

    Parameters
    ----------
    environment : str
        The name of the environment (the environment's domain name).
    regenerate : bool
        If `True`, any secrets that can be generated by the SecretGenerator
        will be regenerated.
    """

    def __init__(self, environment, regenerate):
        super().__init__(environment, regenerate)
        self.op_secrets = {}
        self.op = new_client_from_environment()
        self.parse_vault()

    def parse_vault(self):
        """Parse the 1Password vault and store secrets applicable to this
        environment in the `op_secrets` attribute.

        This method is called automatically when initializing a
        `OnePasswordSecretGenerator`.
        """
        vault = self.op.get_vault_by_title("RSP-Vault")
        items = self.op.get_items(vault.id)

        for item_summary in items:
            key = None
            secret_notes = None
            secret_password = None
            environments = []
            item = self.op.get_item(item_summary.id, vault.id)

            logging.debug(f"Looking at {item.id}")

            for field in item.fields:
                if field.label == "generate_secrets_key":
                    if key is None:
                        key = field.value
                    else:
                        msg = "Found two generate_secrets_keys for {key}"
                        raise Exception(msg)
                elif field.label == "environment":
                    environments.append(field.value)
                elif field.label == "notesPlain":
                    secret_notes = field.value
                elif field.purpose == "PASSWORD":
                    secret_password = field.value

            if not key:
                continue

            secret_value = secret_notes or secret_password

            if not secret_value:
                logging.error("No value found for %s", item.title)
                continue

            logging.debug("Environments are %s for %s", environments, item.id)

            if self.environment in environments:
                self.op_secrets[key] = secret_value
                logging.debug("Storing %s (matching environment)", item.id)
            elif not environments and key not in self.op_secrets:
                self.op_secrets[key] = secret_value
                logging.debug("Storing %s (applicable to all envs)", item.id)
            else:
                logging.debug("Ignoring %s", item.id)

    def input_field(self, component, name, description):
        """Query for a secret's value from 1Password (`op_secrets` attribute).

        This method overrides `SecretGenerator.input_field`, which prompts
        a user interactively.
        """
        key = f"{component} {name}"
        if key not in self.op_secrets:
            raise Exception(f"Did not find entry in 1Password for {key}")

        self.secrets[component][name] = self.op_secrets[key]

    def input_file(self, component, name, description):
        """Query for a secret file from 1Password (`op_secrets` attribute).

        This method overrides `SecretGenerator.input_file`, which prompts
        a user interactively.
        """
        return self.input_field(component, name, description)

    def generate(self):
        """Generate secrets, updating the `secrets` attribute.

        This method first runs `SecretGenerator.generate`, and then
        automatically generates secrets for any additional components
        that were identified in 1Password.

        If a secret appears already, it is overridden with the value in
        1Password.
        """
        super().generate()

        for composite_key, secret_value in self.op_secrets.items():
            item_component, item_name = composite_key.split()
            # Special case for components that may not be present in every
            # environment, but nonetheless might be 1Password secrets (see
            # conditional in SecretGenerator.generate)
            if item_component in {"ingress-nginx", "cert-manager"}:
                continue

            logging.debug(
                "Updating component: %s/%s", item_component, item_name
            )
            self.input_field(item_component, item_name, "")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="generate_secrets")
    parser.add_argument(
        "--op",
        default=False,
        action="store_true",
        help="Load secrets from 1Password",
    )
    parser.add_argument(
        "--verbose", default=False, action="store_true", help="Verbose logging"
    )
    parser.add_argument(
        "--regenerate",
        default=False,
        action="store_true",
        help="Regenerate random secrets",
    )
    parser.add_argument("environment", help="Environment to generate")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig()

    if args.op:
        sg = OnePasswordSecretGenerator(args.environment, args.regenerate)
    else:
        sg = SecretGenerator(args.environment, args.regenerate)

    sg.load()
    sg.generate()
    sg.save()
