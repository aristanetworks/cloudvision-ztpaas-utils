#!/usr/bin/env python3
# Copyright (c) 2024 Arista Networks, Inc. All rights reserved.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the COPYING file.
# FOR INTERNAL USE ONLY. NOT FOR DISTRIBUTION.

import unittest

import jinja2

BOOTSTRAP_DIR = "BootstrapScriptWithToken"
BOOTSTRAP_TEMPLATE_FILE = "bootstrap_template.j2"
BOOTSTRAP_FILE = "bootstrap.py"


def generate_bootstrap_file(params: dict[str, str]) -> str:
    '''Generates the bootstrap file by rendering the given parameters'''
    template_loader = jinja2.FileSystemLoader(searchpath=BOOTSTRAP_DIR)
    template_env = jinja2.Environment(loader=template_loader, keep_trailing_newline=True)
    template = template_env.get_template(BOOTSTRAP_TEMPLATE_FILE)
    return template.render(params)


class BootstrapTest(unittest.TestCase):
    '''Tests Bootstrap File'''

    def test_template_sync(self):
        '''Tests bootstrap file and bootstrap template file are in sync'''
        params = {
            "enrollmentToken": '""',
            "eosUrl": '""',
            "cvproxy": '""',
            "cvAddr": '""',
            "ntpServer": '""',
        }

        gen_file_content = generate_bootstrap_file(params)

        bootstrap_file_path = f"{BOOTSTRAP_DIR}/{BOOTSTRAP_FILE}"
        with open(bootstrap_file_path, "r", encoding="utf-8") as f:
            file_content = f.read()
            self.assertEqual(gen_file_content, file_content)


if __name__ == "__main__":
    unittest.main()
