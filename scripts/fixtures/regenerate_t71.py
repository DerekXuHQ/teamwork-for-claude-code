#!/usr/bin/env python3
"""Regenerate all T71 JSONL samples from local source transcripts.

Usage: python3 scripts/fixtures/regenerate_t71.py REAL_CANDIDATE REAL_BRIDGE REAL_TOOL_RESULTS REAL_RECORDS
Source paths are arguments and are never written into this repository.
"""
import getpass, hashlib, json, re, socket, sys
from pathlib import Path
from pprint import pformat

OUT = Path(__file__).resolve().parent
LOCAL_SESSION = "00000000-0000-4000-8000-000000000001"

# BEGIN GENERATED SIGNATURE MANIFEST
SIGNATURE_MANIFEST = {'bridge-transcript-cse_fixture-remote-session.jsonl': {'line_count': 2,
                                                        'rows': [{'sha256': '88baedb97451b864a0b94232dd5768201ebc1b038f56ca0b6c2c109e47f11487',
                                                                  'structure': {'keys': {'request': {'keys': {'mode': {'type': 'string'},
                                                                                                              'subtype': {'type': 'string'}},
                                                                                                     'type': 'object'},
                                                                                         'request_id': {'type': 'string'},
                                                                                         'type': {'type': 'string'},
                                                                                         'uuid': {'type': 'string'}},
                                                                                'type': 'object'}},
                                                                 {'sha256': 'fb3ba6687b3b2d8ad3269df071be4e456177cc071803633057afa9ca59e59d60',
                                                                  'structure': {'keys': {'hook_event': {'type': 'string'},
                                                                                         'hook_id': {'type': 'string'},
                                                                                         'hook_name': {'type': 'string'},
                                                                                         'session_id': {'type': 'string'},
                                                                                         'subtype': {'type': 'string'},
                                                                                         'type': {'type': 'string'},
                                                                                         'uuid': {'type': 'string'}},
                                                                                'type': 'object'}}]},
 't71_old_thread.jsonl': {'line_count': 4,
                          'rows': [{'sha256': '6a50d382b32a413f40e17a10d3a1c0349b722355ef07e4de4be961b38b1f40eb',
                                    'structure': {'keys': {'clientPlatform': {'type': 'string'},
                                                           'cwd': {'type': 'string'},
                                                           'entrypoint': {'type': 'string'},
                                                           'gitBranch': {'type': 'string'},
                                                           'isMeta': {'type': 'boolean'},
                                                           'isSidechain': {'type': 'boolean'},
                                                           'message': {'keys': {'content': {'type': 'string'},
                                                                                'role': {'type': 'string'}},
                                                                       'type': 'object'},
                                                           'parentUuid': {'type': 'string'},
                                                           'permissionMode': {'type': 'string'},
                                                           'promptId': {'type': 'string'},
                                                           'promptSource': {'type': 'string'},
                                                           'queueSkipAttachments': {'type': 'boolean'},
                                                           'sessionId': {'type': 'string'},
                                                           'timestamp': {'type': 'string'},
                                                           'turnOrigin': {'type': 'string'},
                                                           'turnPosition': {'keys': {'promptIndex': {'type': 'integer'},
                                                                                     'turnIndex': {'type': 'integer'}},
                                                                            'type': 'object'},
                                                           'turnPromptId': {'type': 'string'},
                                                           'type': {'type': 'string'},
                                                           'userType': {'type': 'string'},
                                                           'uuid': {'type': 'string'},
                                                           'version': {'type': 'string'}},
                                                  'type': 'object'}},
                                   {'sha256': 'f68516a34dbeb4243e3cae1aa4a842a82a4c6f4451a2132d24e70d3f56f20d28',
                                    'structure': {'keys': {'clientPlatform': {'type': 'string'},
                                                           'cwd': {'type': 'string'},
                                                           'entrypoint': {'type': 'string'},
                                                           'gitBranch': {'type': 'string'},
                                                           'isMeta': {'type': 'boolean'},
                                                           'isSidechain': {'type': 'boolean'},
                                                           'message': {'keys': {'content': {'type': 'string'},
                                                                                'role': {'type': 'string'}},
                                                                       'type': 'object'},
                                                           'parentUuid': {'type': 'string'},
                                                           'permissionMode': {'type': 'string'},
                                                           'promptId': {'type': 'string'},
                                                           'promptSource': {'type': 'string'},
                                                           'queueSkipAttachments': {'type': 'boolean'},
                                                           'sessionId': {'type': 'string'},
                                                           'timestamp': {'type': 'string'},
                                                           'turnOrigin': {'type': 'string'},
                                                           'turnPosition': {'keys': {'promptIndex': {'type': 'integer'},
                                                                                     'turnIndex': {'type': 'integer'}},
                                                                            'type': 'object'},
                                                           'turnPromptId': {'type': 'string'},
                                                           'type': {'type': 'string'},
                                                           'userType': {'type': 'string'},
                                                           'uuid': {'type': 'string'},
                                                           'version': {'type': 'string'}},
                                                  'type': 'object'}},
                                   {'sha256': 'c503292300d6dcfa3819159b2865567af9e7bfade55144f3114b19cf4704d74c',
                                    'structure': {'keys': {'advisorModel': {'type': 'string'},
                                                           'apiBlockIndex': {'type': 'integer'},
                                                           'cwd': {'type': 'string'},
                                                           'effort': {'type': 'string'},
                                                           'entrypoint': {'type': 'string'},
                                                           'gitBranch': {'type': 'string'},
                                                           'isSidechain': {'type': 'boolean'},
                                                           'madeBy': {'type': 'string'},
                                                           'message': {'keys': {'container': {'type': 'null'},
                                                                                'content': {'items': [{'keys': {'caller': {'keys': {'type': {'type': 'string'}},
                                                                                                                           'type': 'object'},
                                                                                                                'id': {'type': 'string'},
                                                                                                                'input': {'keys': {'command': {'type': 'string'},
                                                                                                                                   'description': {'type': 'string'}},
                                                                                                                          'type': 'object'},
                                                                                                                'name': {'type': 'string'},
                                                                                                                'type': {'type': 'string'}},
                                                                                                       'type': 'object'}],
                                                                                            'type': 'array'},
                                                                                'context_management': {'type': 'null'},
                                                                                'diagnostics': {'type': 'null'},
                                                                                'id': {'type': 'string'},
                                                                                'input_transformations': {'items': [],
                                                                                                          'type': 'array'},
                                                                                'model': {'type': 'string'},
                                                                                'role': {'type': 'string'},
                                                                                'stop_details': {'type': 'null'},
                                                                                'stop_reason': {'type': 'string'},
                                                                                'stop_sequence': {'type': 'null'},
                                                                                'type': {'type': 'string'},
                                                                                'usage': {'keys': {'cache_creation': {'keys': {'ephemeral_1h_input_tokens': {'type': 'integer'},
                                                                                                                               'ephemeral_5m_input_tokens': {'type': 'integer'}},
                                                                                                                      'type': 'object'},
                                                                                                   'cache_creation_input_tokens': {'type': 'integer'},
                                                                                                   'cache_read_input_tokens': {'type': 'integer'},
                                                                                                   'fallback_credit': {'type': 'null'},
                                                                                                   'inference_geo': {'type': 'string'},
                                                                                                   'input_tokens': {'type': 'integer'},
                                                                                                   'iterations': {'items': [{'keys': {'cache_creation': {'keys': {'ephemeral_1h_input_tokens': {'type': 'integer'},
                                                                                                                                                                  'ephemeral_5m_input_tokens': {'type': 'integer'}},
                                                                                                                                                         'type': 'object'},
                                                                                                                                      'cache_creation_input_tokens': {'type': 'integer'},
                                                                                                                                      'cache_read_input_tokens': {'type': 'integer'},
                                                                                                                                      'input_tokens': {'type': 'integer'},
                                                                                                                                      'output_tokens': {'type': 'integer'},
                                                                                                                                      'type': {'type': 'string'}},
                                                                                                                             'type': 'object'}],
                                                                                                                  'type': 'array'},
                                                                                                   'output_tokens': {'type': 'integer'},
                                                                                                   'output_tokens_details': {'keys': {'thinking_tokens': {'type': 'integer'}},
                                                                                                                             'type': 'object'},
                                                                                                   'server_tool_use': {'keys': {'web_fetch_requests': {'type': 'integer'},
                                                                                                                                'web_search_requests': {'type': 'integer'}},
                                                                                                                       'type': 'object'},
                                                                                                   'service_tier': {'type': 'string'},
                                                                                                   'speed': {'type': 'string'}},
                                                                                          'type': 'object'}},
                                                                       'type': 'object'},
                                                           'parentUuid': {'type': 'string'},
                                                           'perTurnEffort': {'type': 'string'},
                                                           'promptCacheRequested': {'type': 'string'},
                                                           'requestId': {'type': 'string'},
                                                           'requestedModel': {'type': 'string'},
                                                           'serverClassifierRequest': {'type': 'string'},
                                                           'sessionId': {'type': 'string'},
                                                           'timestamp': {'type': 'string'},
                                                           'toolInputStreamMs': {'type': 'integer'},
                                                           'type': {'type': 'string'},
                                                           'userType': {'type': 'string'},
                                                           'uuid': {'type': 'string'},
                                                           'version': {'type': 'string'},
                                                           'wireToolInputs': {'keys': {'fixture-tool-id': {'keys': {'command': {'type': 'string'},
                                                                                                                    'description': {'type': 'string'}},
                                                                                                           'type': 'object'}},
                                                                              'type': 'object'}},
                                                  'type': 'object'}},
                                   {'sha256': 'f0a6fe64f91cf7fb76dbba039b14e9bdd91755f0c87d7b54caa5edf9c8d1c257',
                                    'structure': {'keys': {'cwd': {'type': 'string'},
                                                           'entrypoint': {'type': 'string'},
                                                           'gitBranch': {'type': 'string'},
                                                           'isSidechain': {'type': 'boolean'},
                                                           'message': {'keys': {'content': {'items': [{'keys': {'content': {'type': 'string'},
                                                                                                                'is_error': {'type': 'boolean'},
                                                                                                                'tool_use_id': {'type': 'string'},
                                                                                                                'type': {'type': 'string'}},
                                                                                                       'type': 'object'}],
                                                                                            'type': 'array'},
                                                                                'role': {'type': 'string'}},
                                                                       'type': 'object'},
                                                           'parentUuid': {'type': 'string'},
                                                           'permissionDecision': {'keys': {'decision': {'type': 'string'},
                                                                                           'reasonType': {'type': 'string'},
                                                                                           'source': {'type': 'string'}},
                                                                                  'type': 'object'},
                                                           'promptId': {'type': 'string'},
                                                           'sandboxed': {'type': 'boolean'},
                                                           'serverClassifierContext': {'keys': {'context': {'keys': {'git_state': {'keys': {'branch': {'type': 'null'},
                                                                                                                                            'cwd': {'type': 'string'},
                                                                                                                                            'default_branch': {'type': 'null'},
                                                                                                                                            'error': {'type': 'string'},
                                                                                                                                            'root': {'type': 'null'},
                                                                                                                                            'status': {'type': 'null'},
                                                                                                                                            'visibility': {'keys': {'origin': {'type': 'null'},
                                                                                                                                                                    'push_remote': {'type': 'null'},
                                                                                                                                                                    'remotes': {'items': [],
                                                                                                                                                                                'type': 'array'},
                                                                                                                                                                    'visibility_cache': {'items': [],
                                                                                                                                                                                         'type': 'array'}},
                                                                                                                                                           'type': 'object'}},
                                                                                                                                   'type': 'object'},
                                                                                                                     'live_cwd': {'type': 'string'},
                                                                                                                     'platform': {'type': 'string'}},
                                                                                                            'type': 'object'},
                                                                                                'request': {'type': 'string'}},
                                                                                       'type': 'object'},
                                                           'sessionId': {'type': 'string'},
                                                           'sourceToolAssistantUUID': {'type': 'string'},
                                                           'timestamp': {'type': 'string'},
                                                           'toolUseResult': {'keys': {'interrupted': {'type': 'boolean'},
                                                                                      'isImage': {'type': 'boolean'},
                                                                                      'noOutputExpected': {'type': 'boolean'},
                                                                                      'stderr': {'type': 'string'},
                                                                                      'stdout': {'type': 'string'}},
                                                                             'type': 'object'},
                                                           'type': {'type': 'string'},
                                                           'userType': {'type': 'string'},
                                                           'uuid': {'type': 'string'},
                                                           'version': {'type': 'string'}},
                                                  'type': 'object'}}]},
 't71_real_candidate.jsonl': {'line_count': 4,
                              'rows': [{'sha256': '9e8db9af7888efdde1da32cb5513541714e8364226ce1fddfedee75c571378cc',
                                        'structure': {'keys': {'isSnapshotUpdate': {'type': 'boolean'},
                                                               'messageId': {'type': 'string'},
                                                               'snapshot': {'keys': {'messageId': {'type': 'string'},
                                                                                     'timestamp': {'type': 'string'},
                                                                                     'trackedFileBackups': {'keys': {},
                                                                                                            'type': 'object'}},
                                                                            'type': 'object'},
                                                               'type': {'type': 'string'}},
                                                      'type': 'object'}},
                                       {'sha256': '5180602f1cbf3f362717dedda64c8a701350fa1f2b2e3f605b65e531c6f980ce',
                                        'structure': {'keys': {'clientPlatform': {'type': 'string'},
                                                               'cwd': {'type': 'string'},
                                                               'entrypoint': {'type': 'string'},
                                                               'gitBranch': {'type': 'string'},
                                                               'isMeta': {'type': 'boolean'},
                                                               'isSidechain': {'type': 'boolean'},
                                                               'message': {'keys': {'content': {'type': 'string'},
                                                                                    'role': {'type': 'string'}},
                                                                           'type': 'object'},
                                                               'parentUuid': {'type': 'string'},
                                                               'permissionMode': {'type': 'string'},
                                                               'promptId': {'type': 'string'},
                                                               'promptSource': {'type': 'string'},
                                                               'queueSkipAttachments': {'type': 'boolean'},
                                                               'sessionId': {'type': 'string'},
                                                               'timestamp': {'type': 'string'},
                                                               'turnOrigin': {'type': 'string'},
                                                               'turnPosition': {'keys': {'promptIndex': {'type': 'integer'},
                                                                                         'turnIndex': {'type': 'integer'}},
                                                                                'type': 'object'},
                                                               'turnPromptId': {'type': 'string'},
                                                               'type': {'type': 'string'},
                                                               'userType': {'type': 'string'},
                                                               'uuid': {'type': 'string'},
                                                               'version': {'type': 'string'}},
                                                      'type': 'object'}},
                                       {'sha256': '7ea18bb46600b6abb5aa2a3a2af1aab602a75dd3698729776c5ee7ea9559bde1',
                                        'structure': {'keys': {'advisorModel': {'type': 'string'},
                                                               'apiBlockIndex': {'type': 'integer'},
                                                               'cwd': {'type': 'string'},
                                                               'effort': {'type': 'string'},
                                                               'entrypoint': {'type': 'string'},
                                                               'gitBranch': {'type': 'string'},
                                                               'isSidechain': {'type': 'boolean'},
                                                               'madeBy': {'type': 'string'},
                                                               'message': {'keys': {'container': {'type': 'null'},
                                                                                    'content': {'items': [{'keys': {'caller': {'keys': {'type': {'type': 'string'}},
                                                                                                                               'type': 'object'},
                                                                                                                    'id': {'type': 'string'},
                                                                                                                    'input': {'keys': {'message': {'type': 'string'},
                                                                                                                                       'session_id': {'type': 'string'}},
                                                                                                                              'type': 'object'},
                                                                                                                    'name': {'type': 'string'},
                                                                                                                    'type': {'type': 'string'}},
                                                                                                           'type': 'object'}],
                                                                                                'type': 'array'},
                                                                                    'context_management': {'type': 'null'},
                                                                                    'diagnostics': {'type': 'null'},
                                                                                    'id': {'type': 'string'},
                                                                                    'input_transformations': {'items': [],
                                                                                                              'type': 'array'},
                                                                                    'model': {'type': 'string'},
                                                                                    'role': {'type': 'string'},
                                                                                    'stop_details': {'type': 'null'},
                                                                                    'stop_reason': {'type': 'string'},
                                                                                    'stop_sequence': {'type': 'null'},
                                                                                    'type': {'type': 'string'},
                                                                                    'usage': {'keys': {'cache_creation': {'keys': {'ephemeral_1h_input_tokens': {'type': 'integer'},
                                                                                                                                   'ephemeral_5m_input_tokens': {'type': 'integer'}},
                                                                                                                          'type': 'object'},
                                                                                                       'cache_creation_input_tokens': {'type': 'integer'},
                                                                                                       'cache_read_input_tokens': {'type': 'integer'},
                                                                                                       'fallback_credit': {'type': 'null'},
                                                                                                       'inference_geo': {'type': 'string'},
                                                                                                       'input_tokens': {'type': 'integer'},
                                                                                                       'iterations': {'items': [{'keys': {'cache_creation': {'keys': {'ephemeral_1h_input_tokens': {'type': 'integer'},
                                                                                                                                                                      'ephemeral_5m_input_tokens': {'type': 'integer'}},
                                                                                                                                                             'type': 'object'},
                                                                                                                                          'cache_creation_input_tokens': {'type': 'integer'},
                                                                                                                                          'cache_read_input_tokens': {'type': 'integer'},
                                                                                                                                          'input_tokens': {'type': 'integer'},
                                                                                                                                          'output_tokens': {'type': 'integer'},
                                                                                                                                          'type': {'type': 'string'}},
                                                                                                                                 'type': 'object'}],
                                                                                                                      'type': 'array'},
                                                                                                       'output_tokens': {'type': 'integer'},
                                                                                                       'output_tokens_details': {'keys': {'thinking_tokens': {'type': 'integer'}},
                                                                                                                                 'type': 'object'},
                                                                                                       'server_tool_use': {'keys': {'web_fetch_requests': {'type': 'integer'},
                                                                                                                                    'web_search_requests': {'type': 'integer'}},
                                                                                                                           'type': 'object'},
                                                                                                       'service_tier': {'type': 'string'},
                                                                                                       'speed': {'type': 'string'}},
                                                                                              'type': 'object'}},
                                                                           'type': 'object'},
                                                               'parentUuid': {'type': 'string'},
                                                               'perTurnEffort': {'type': 'string'},
                                                               'promptCacheRequested': {'type': 'string'},
                                                               'requestId': {'type': 'string'},
                                                               'requestedModel': {'type': 'string'},
                                                               'serverClassifierRequest': {'type': 'string'},
                                                               'sessionId': {'type': 'string'},
                                                               'timestamp': {'type': 'string'},
                                                               'toolInputStreamMs': {'type': 'integer'},
                                                               'type': {'type': 'string'},
                                                               'userType': {'type': 'string'},
                                                               'uuid': {'type': 'string'},
                                                               'version': {'type': 'string'},
                                                               'wireToolInputs': {'keys': {'fixture-tool-id': {'keys': {'message': {'type': 'string'},
                                                                                                                        'session_id': {'type': 'string'}},
                                                                                                               'type': 'object'}},
                                                                                  'type': 'object'}},
                                                      'type': 'object'}},
                                       {'sha256': '3ce1af45877f5ad469ea4cce23b2fb9e20240ab226f070986197e968159e1603',
                                        'structure': {'keys': {'cwd': {'type': 'string'},
                                                               'entrypoint': {'type': 'string'},
                                                               'gitBranch': {'type': 'string'},
                                                               'isSidechain': {'type': 'boolean'},
                                                               'mcpCallNotReadOnly': {'type': 'string'},
                                                               'message': {'keys': {'content': {'items': [{'keys': {'content': {'items': [{'keys': {'text': {'type': 'string'},
                                                                                                                                                    'type': {'type': 'string'}},
                                                                                                                                           'type': 'object'}],
                                                                                                                                'type': 'array'},
                                                                                                                    'tool_use_id': {'type': 'string'},
                                                                                                                    'type': {'type': 'string'}},
                                                                                                           'type': 'object'}],
                                                                                                'type': 'array'},
                                                                                    'role': {'type': 'string'}},
                                                                           'type': 'object'},
                                                               'parentUuid': {'type': 'string'},
                                                               'permissionDecision': {'keys': {'decision': {'type': 'string'},
                                                                                               'reasonType': {'type': 'string'},
                                                                                               'source': {'type': 'string'}},
                                                                                      'type': 'object'},
                                                               'promptId': {'type': 'string'},
                                                               'serverClassifierContext': {'keys': {'context': {'keys': {'git_state': {'keys': {'branch': {'type': 'string'},
                                                                                                                                                'cwd': {'type': 'string'},
                                                                                                                                                'default_branch': {'type': 'null'},
                                                                                                                                                'root': {'type': 'string'},
                                                                                                                                                'status': {'keys': {'clean': {'type': 'boolean'},
                                                                                                                                                                    'counts': {'keys': {'modified': {'type': 'integer'},
                                                                                                                                                                                        'staged': {'type': 'integer'},
                                                                                                                                                                                        'untracked': {'type': 'null'},
                                                                                                                                                                                        'untracked_normal': {'type': 'integer'}},
                                                                                                                                                                               'type': 'object'},
                                                                                                                                                                    'porcelain': {'type': 'null'},
                                                                                                                                                                    'truncated': {'type': 'boolean'}},
                                                                                                                                                           'type': 'object'},
                                                                                                                                                'visibility': {'keys': {'origin': {'type': 'null'},
                                                                                                                                                                        'push_remote': {'type': 'string'},
                                                                                                                                                                        'remotes': {'items': [],
                                                                                                                                                                                    'type': 'array'},
                                                                                                                                                                        'visibility_cache': {'items': [],
                                                                                                                                                                                             'type': 'array'}},
                                                                                                                                                               'type': 'object'}},
                                                                                                                                       'type': 'object'},
                                                                                                                         'live_cwd': {'type': 'string'},
                                                                                                                         'platform': {'type': 'string'}},
                                                                                                                'type': 'object'},
                                                                                                    'request': {'type': 'string'}},
                                                                                           'type': 'object'},
                                                               'sessionId': {'type': 'string'},
                                                               'sourceToolAssistantUUID': {'type': 'string'},
                                                               'timestamp': {'type': 'string'},
                                                               'toolUseResult': {'items': [{'keys': {'text': {'type': 'string'},
                                                                                                     'type': {'type': 'string'}},
                                                                                            'type': 'object'}],
                                                                                 'type': 'array'},
                                                               'type': {'type': 'string'},
                                                               'userType': {'type': 'string'},
                                                               'uuid': {'type': 'string'},
                                                               'version': {'type': 'string'}},
                                                      'type': 'object'}}]},
 't71_real_tool_results.jsonl': {'line_count': 10,
                                 'rows': [{'sha256': '909d5f65bf79a433892ab0f7165613d3eaf35aa470976f9a82b9ab42bf881896',
                                           'structure': {'keys': {'advisorModel': {'type': 'string'},
                                                                  'apiBlockIndex': {'type': 'integer'},
                                                                  'cwd': {'type': 'string'},
                                                                  'effort': {'type': 'string'},
                                                                  'gitBranch': {'type': 'string'},
                                                                  'isSidechain': {'type': 'boolean'},
                                                                  'madeBy': {'type': 'string'},
                                                                  'message': {'keys': {'container': {'type': 'null'},
                                                                                       'content': {'items': [{'keys': {'caller': {'keys': {'type': {'type': 'string'}},
                                                                                                                                  'type': 'object'},
                                                                                                                       'id': {'type': 'string'},
                                                                                                                       'input': {'keys': {'kinds': {'items': [{'type': 'string'},
                                                                                                                                                              {'type': 'string'}],
                                                                                                                                                    'type': 'array'},
                                                                                                                                          'limit': {'type': 'integer'},
                                                                                                                                          'session_id': {'type': 'string'}},
                                                                                                                                 'type': 'object'},
                                                                                                                       'name': {'type': 'string'},
                                                                                                                       'type': {'type': 'string'}},
                                                                                                              'type': 'object'}],
                                                                                                   'type': 'array'},
                                                                                       'context_management': {'type': 'null'},
                                                                                       'diagnostics': {'type': 'null'},
                                                                                       'id': {'type': 'string'},
                                                                                       'input_transformations': {'items': [],
                                                                                                                 'type': 'array'},
                                                                                       'model': {'type': 'string'},
                                                                                       'role': {'type': 'string'},
                                                                                       'stop_details': {'type': 'null'},
                                                                                       'stop_reason': {'type': 'string'},
                                                                                       'stop_sequence': {'type': 'null'},
                                                                                       'type': {'type': 'string'},
                                                                                       'usage': {'keys': {'cache_creation': {'keys': {'ephemeral_1h_input_tokens': {'type': 'integer'},
                                                                                                                                      'ephemeral_5m_input_tokens': {'type': 'integer'}},
                                                                                                                             'type': 'object'},
                                                                                                          'cache_creation_input_tokens': {'type': 'integer'},
                                                                                                          'cache_read_input_tokens': {'type': 'integer'},
                                                                                                          'fallback_credit': {'type': 'null'},
                                                                                                          'inference_geo': {'type': 'string'},
                                                                                                          'input_tokens': {'type': 'integer'},
                                                                                                          'iterations': {'items': [{'keys': {'cache_creation': {'keys': {'ephemeral_1h_input_tokens': {'type': 'integer'},
                                                                                                                                                                         'ephemeral_5m_input_tokens': {'type': 'integer'}},
                                                                                                                                                                'type': 'object'},
                                                                                                                                             'cache_creation_input_tokens': {'type': 'integer'},
                                                                                                                                             'cache_read_input_tokens': {'type': 'integer'},
                                                                                                                                             'input_tokens': {'type': 'integer'},
                                                                                                                                             'output_tokens': {'type': 'integer'},
                                                                                                                                             'type': {'type': 'string'}},
                                                                                                                                    'type': 'object'}],
                                                                                                                         'type': 'array'},
                                                                                                          'output_tokens': {'type': 'integer'},
                                                                                                          'output_tokens_details': {'keys': {'thinking_tokens': {'type': 'integer'}},
                                                                                                                                    'type': 'object'},
                                                                                                          'server_tool_use': {'keys': {'web_fetch_requests': {'type': 'integer'},
                                                                                                                                       'web_search_requests': {'type': 'integer'}},
                                                                                                                              'type': 'object'},
                                                                                                          'service_tier': {'type': 'string'},
                                                                                                          'speed': {'type': 'string'}},
                                                                                                 'type': 'object'}},
                                                                              'type': 'object'},
                                                                  'parentUuid': {'type': 'string'},
                                                                  'perTurnEffort': {'type': 'string'},
                                                                  'promptCacheRequested': {'type': 'string'},
                                                                  'requestedModel': {'type': 'string'},
                                                                  'serverClassifierRequest': {'type': 'string'},
                                                                  'sessionId': {'type': 'string'},
                                                                  'slug': {'type': 'string'},
                                                                  'timestamp': {'type': 'string'},
                                                                  'toolInputStreamMs': {'type': 'integer'},
                                                                  'type': {'type': 'string'},
                                                                  'uuid': {'type': 'string'},
                                                                  'wireToolInputs': {'keys': {'fixture-tool-id': {'keys': {'kinds': {'items': [{'type': 'string'},
                                                                                                                                               {'type': 'string'}],
                                                                                                                                     'type': 'array'},
                                                                                                                           'limit': {'type': 'integer'},
                                                                                                                           'session_id': {'type': 'string'}},
                                                                                                                  'type': 'object'}},
                                                                                     'type': 'object'}},
                                                         'type': 'object'}},
                                          {'sha256': '41022414df2b591c47607e26a9c92ee4b9690d983371f0f75955952db487e6f2',
                                           'structure': {'keys': {'cwd': {'type': 'string'},
                                                                  'gitBranch': {'type': 'string'},
                                                                  'isSidechain': {'type': 'boolean'},
                                                                  'message': {'keys': {'content': {'items': [{'keys': {'content': {'items': [{'keys': {'text': {'type': 'string'},
                                                                                                                                                       'type': {'type': 'string'}},
                                                                                                                                              'type': 'object'}],
                                                                                                                                   'type': 'array'},
                                                                                                                       'tool_use_id': {'type': 'string'},
                                                                                                                       'type': {'type': 'string'}},
                                                                                                              'type': 'object'}],
                                                                                                   'type': 'array'},
                                                                                       'role': {'type': 'string'}},
                                                                              'type': 'object'},
                                                                  'parentUuid': {'type': 'string'},
                                                                  'permissionDecision': {'keys': {'decision': {'type': 'string'},
                                                                                                  'reasonType': {'type': 'string'},
                                                                                                  'source': {'type': 'string'}},
                                                                                         'type': 'object'},
                                                                  'sessionId': {'type': 'string'},
                                                                  'slug': {'type': 'string'},
                                                                  'sourceToolAssistantUUID': {'type': 'string'},
                                                                  'timestamp': {'type': 'string'},
                                                                  'toolUseResult': {'items': [{'keys': {'text': {'type': 'string'},
                                                                                                        'type': {'type': 'string'}},
                                                                                               'type': 'object'}],
                                                                                    'type': 'array'},
                                                                  'type': {'type': 'string'},
                                                                  'uuid': {'type': 'string'}},
                                                         'type': 'object'}},
                                          {'sha256': '105ee7ef95976317c3f5fbdf757cdd3a46ecd7a5c6194f29dfefea61b2dc6402',
                                           'structure': {'keys': {'advisorModel': {'type': 'string'},
                                                                  'apiBlockIndex': {'type': 'integer'},
                                                                  'cwd': {'type': 'string'},
                                                                  'effort': {'type': 'string'},
                                                                  'gitBranch': {'type': 'string'},
                                                                  'isSidechain': {'type': 'boolean'},
                                                                  'madeBy': {'type': 'string'},
                                                                  'message': {'keys': {'container': {'type': 'null'},
                                                                                       'content': {'items': [{'keys': {'caller': {'keys': {'type': {'type': 'string'}},
                                                                                                                                  'type': 'object'},
                                                                                                                       'id': {'type': 'string'},
                                                                                                                       'input': {'keys': {'session_id': {'type': 'string'}},
                                                                                                                                 'type': 'object'},
                                                                                                                       'name': {'type': 'string'},
                                                                                                                       'type': {'type': 'string'}},
                                                                                                              'type': 'object'}],
                                                                                                   'type': 'array'},
                                                                                       'context_management': {'type': 'null'},
                                                                                       'diagnostics': {'type': 'null'},
                                                                                       'id': {'type': 'string'},
                                                                                       'input_transformations': {'items': [],
                                                                                                                 'type': 'array'},
                                                                                       'model': {'type': 'string'},
                                                                                       'role': {'type': 'string'},
                                                                                       'stop_details': {'type': 'null'},
                                                                                       'stop_reason': {'type': 'string'},
                                                                                       'stop_sequence': {'type': 'null'},
                                                                                       'type': {'type': 'string'},
                                                                                       'usage': {'keys': {'cache_creation': {'keys': {'ephemeral_1h_input_tokens': {'type': 'integer'},
                                                                                                                                      'ephemeral_5m_input_tokens': {'type': 'integer'}},
                                                                                                                             'type': 'object'},
                                                                                                          'cache_creation_input_tokens': {'type': 'integer'},
                                                                                                          'cache_read_input_tokens': {'type': 'integer'},
                                                                                                          'fallback_credit': {'type': 'null'},
                                                                                                          'inference_geo': {'type': 'string'},
                                                                                                          'input_tokens': {'type': 'integer'},
                                                                                                          'iterations': {'items': [{'keys': {'cache_creation': {'keys': {'ephemeral_1h_input_tokens': {'type': 'integer'},
                                                                                                                                                                         'ephemeral_5m_input_tokens': {'type': 'integer'}},
                                                                                                                                                                'type': 'object'},
                                                                                                                                             'cache_creation_input_tokens': {'type': 'integer'},
                                                                                                                                             'cache_read_input_tokens': {'type': 'integer'},
                                                                                                                                             'input_tokens': {'type': 'integer'},
                                                                                                                                             'output_tokens': {'type': 'integer'},
                                                                                                                                             'type': {'type': 'string'}},
                                                                                                                                    'type': 'object'}],
                                                                                                                         'type': 'array'},
                                                                                                          'output_tokens': {'type': 'integer'},
                                                                                                          'output_tokens_details': {'keys': {'thinking_tokens': {'type': 'integer'}},
                                                                                                                                    'type': 'object'},
                                                                                                          'server_tool_use': {'keys': {'web_fetch_requests': {'type': 'integer'},
                                                                                                                                       'web_search_requests': {'type': 'integer'}},
                                                                                                                              'type': 'object'},
                                                                                                          'service_tier': {'type': 'string'},
                                                                                                          'speed': {'type': 'string'}},
                                                                                                 'type': 'object'}},
                                                                              'type': 'object'},
                                                                  'parentUuid': {'type': 'string'},
                                                                  'perTurnEffort': {'type': 'string'},
                                                                  'promptCacheRequested': {'type': 'string'},
                                                                  'requestedModel': {'type': 'string'},
                                                                  'serverClassifierRequest': {'type': 'string'},
                                                                  'sessionId': {'type': 'string'},
                                                                  'slug': {'type': 'string'},
                                                                  'timestamp': {'type': 'string'},
                                                                  'toolInputStreamMs': {'type': 'integer'},
                                                                  'type': {'type': 'string'},
                                                                  'uuid': {'type': 'string'},
                                                                  'wireToolInputs': {'keys': {'fixture-tool-id': {'keys': {'session_id': {'type': 'string'}},
                                                                                                                  'type': 'object'}},
                                                                                     'type': 'object'}},
                                                         'type': 'object'}},
                                          {'sha256': '3d8c975becef8f9240170571452fdf11810eaa51c7b00faa4be168cf79474a14',
                                           'structure': {'keys': {'cwd': {'type': 'string'},
                                                                  'gitBranch': {'type': 'string'},
                                                                  'isSidechain': {'type': 'boolean'},
                                                                  'message': {'keys': {'content': {'items': [{'keys': {'content': {'items': [{'keys': {'text': {'type': 'string'},
                                                                                                                                                       'type': {'type': 'string'}},
                                                                                                                                              'type': 'object'}],
                                                                                                                                   'type': 'array'},
                                                                                                                       'tool_use_id': {'type': 'string'},
                                                                                                                       'type': {'type': 'string'}},
                                                                                                              'type': 'object'}],
                                                                                                   'type': 'array'},
                                                                                       'role': {'type': 'string'}},
                                                                              'type': 'object'},
                                                                  'parentUuid': {'type': 'string'},
                                                                  'permissionDecision': {'keys': {'decision': {'type': 'string'},
                                                                                                  'reasonType': {'type': 'string'},
                                                                                                  'source': {'type': 'string'}},
                                                                                         'type': 'object'},
                                                                  'serverClassifierContext': {'keys': {'context': {'keys': {'git_state': {'keys': {'branch': {'type': 'string'},
                                                                                                                                                   'cwd': {'type': 'string'},
                                                                                                                                                   'default_branch': {'type': 'null'},
                                                                                                                                                   'root': {'type': 'string'},
                                                                                                                                                   'status': {'keys': {'clean': {'type': 'boolean'},
                                                                                                                                                                       'counts': {'keys': {'modified': {'type': 'integer'},
                                                                                                                                                                                           'staged': {'type': 'integer'},
                                                                                                                                                                                           'untracked': {'type': 'null'},
                                                                                                                                                                                           'untracked_normal': {'type': 'integer'}},
                                                                                                                                                                                  'type': 'object'},
                                                                                                                                                                       'porcelain': {'type': 'null'},
                                                                                                                                                                       'truncated': {'type': 'boolean'}},
                                                                                                                                                              'type': 'object'},
                                                                                                                                                   'visibility': {'keys': {'origin': {'type': 'null'},
                                                                                                                                                                           'push_remote': {'type': 'string'},
                                                                                                                                                                           'remotes': {'items': [],
                                                                                                                                                                                       'type': 'array'},
                                                                                                                                                                           'visibility_cache': {'items': [{'keys': {'host': {'type': 'string'},
                                                                                                                                                                                                                    'remote': {'type': 'string'},
                                                                                                                                                                                                                    'visibility': {'type': 'string'}},
                                                                                                                                                                                                           'type': 'object'}],
                                                                                                                                                                                                'type': 'array'}},
                                                                                                                                                                  'type': 'object'}},
                                                                                                                                          'type': 'object'},
                                                                                                                            'live_cwd': {'type': 'string'},
                                                                                                                            'platform': {'type': 'string'}},
                                                                                                                   'type': 'object'},
                                                                                                       'request': {'type': 'string'}},
                                                                                              'type': 'object'},
                                                                  'sessionId': {'type': 'string'},
                                                                  'slug': {'type': 'string'},
                                                                  'sourceToolAssistantUUID': {'type': 'string'},
                                                                  'timestamp': {'type': 'string'},
                                                                  'toolUseResult': {'items': [{'keys': {'text': {'type': 'string'},
                                                                                                        'type': {'type': 'string'}},
                                                                                               'type': 'object'}],
                                                                                    'type': 'array'},
                                                                  'type': {'type': 'string'},
                                                                  'uuid': {'type': 'string'}},
                                                         'type': 'object'}},
                                          {'sha256': '7bbc80a5c39330d3cddb21ac2f6fa459e285b8b8747c68dcca7b50b5dcf7b7e5',
                                           'structure': {'keys': {'advisorModel': {'type': 'string'},
                                                                  'apiBlockIndex': {'type': 'integer'},
                                                                  'cwd': {'type': 'string'},
                                                                  'effort': {'type': 'string'},
                                                                  'entrypoint': {'type': 'string'},
                                                                  'gitBranch': {'type': 'string'},
                                                                  'isSidechain': {'type': 'boolean'},
                                                                  'madeBy': {'type': 'string'},
                                                                  'message': {'keys': {'container': {'type': 'null'},
                                                                                       'content': {'items': [{'keys': {'caller': {'keys': {'type': {'type': 'string'}},
                                                                                                                                  'type': 'object'},
                                                                                                                       'id': {'type': 'string'},
                                                                                                                       'input': {'keys': {'kinds': {'items': [{'type': 'string'}],
                                                                                                                                                    'type': 'array'},
                                                                                                                                          'limit': {'type': 'integer'},
                                                                                                                                          'session_id': {'type': 'string'}},
                                                                                                                                 'type': 'object'},
                                                                                                                       'name': {'type': 'string'},
                                                                                                                       'type': {'type': 'string'}},
                                                                                                              'type': 'object'}],
                                                                                                   'type': 'array'},
                                                                                       'context_management': {'type': 'null'},
                                                                                       'diagnostics': {'type': 'null'},
                                                                                       'id': {'type': 'string'},
                                                                                       'input_transformations': {'items': [],
                                                                                                                 'type': 'array'},
                                                                                       'model': {'type': 'string'},
                                                                                       'role': {'type': 'string'},
                                                                                       'stop_details': {'type': 'null'},
                                                                                       'stop_reason': {'type': 'string'},
                                                                                       'stop_sequence': {'type': 'null'},
                                                                                       'type': {'type': 'string'},
                                                                                       'usage': {'keys': {'cache_creation': {'keys': {'ephemeral_1h_input_tokens': {'type': 'integer'},
                                                                                                                                      'ephemeral_5m_input_tokens': {'type': 'integer'}},
                                                                                                                             'type': 'object'},
                                                                                                          'cache_creation_input_tokens': {'type': 'integer'},
                                                                                                          'cache_read_input_tokens': {'type': 'integer'},
                                                                                                          'fallback_credit': {'type': 'null'},
                                                                                                          'inference_geo': {'type': 'string'},
                                                                                                          'input_tokens': {'type': 'integer'},
                                                                                                          'iterations': {'items': [{'keys': {'cache_creation': {'keys': {'ephemeral_1h_input_tokens': {'type': 'integer'},
                                                                                                                                                                         'ephemeral_5m_input_tokens': {'type': 'integer'}},
                                                                                                                                                                'type': 'object'},
                                                                                                                                             'cache_creation_input_tokens': {'type': 'integer'},
                                                                                                                                             'cache_read_input_tokens': {'type': 'integer'},
                                                                                                                                             'input_tokens': {'type': 'integer'},
                                                                                                                                             'output_tokens': {'type': 'integer'},
                                                                                                                                             'type': {'type': 'string'}},
                                                                                                                                    'type': 'object'}],
                                                                                                                         'type': 'array'},
                                                                                                          'output_tokens': {'type': 'integer'},
                                                                                                          'output_tokens_details': {'keys': {'thinking_tokens': {'type': 'integer'}},
                                                                                                                                    'type': 'object'},
                                                                                                          'server_tool_use': {'keys': {'web_fetch_requests': {'type': 'integer'},
                                                                                                                                       'web_search_requests': {'type': 'integer'}},
                                                                                                                              'type': 'object'},
                                                                                                          'service_tier': {'type': 'string'},
                                                                                                          'speed': {'type': 'string'}},
                                                                                                 'type': 'object'}},
                                                                              'type': 'object'},
                                                                  'parentUuid': {'type': 'string'},
                                                                  'perTurnEffort': {'type': 'string'},
                                                                  'promptCacheRequested': {'type': 'string'},
                                                                  'requestId': {'type': 'string'},
                                                                  'requestedModel': {'type': 'string'},
                                                                  'serverClassifierRequest': {'type': 'string'},
                                                                  'sessionId': {'type': 'string'},
                                                                  'slug': {'type': 'string'},
                                                                  'timestamp': {'type': 'string'},
                                                                  'toolInputStreamMs': {'type': 'integer'},
                                                                  'type': {'type': 'string'},
                                                                  'userType': {'type': 'string'},
                                                                  'uuid': {'type': 'string'},
                                                                  'version': {'type': 'string'},
                                                                  'wireToolInputs': {'keys': {'fixture-tool-id': {'keys': {'kinds': {'items': [{'type': 'string'}],
                                                                                                                                     'type': 'array'},
                                                                                                                           'limit': {'type': 'integer'},
                                                                                                                           'session_id': {'type': 'string'}},
                                                                                                                  'type': 'object'}},
                                                                                     'type': 'object'}},
                                                         'type': 'object'}},
                                          {'sha256': '3b1ac106c0081da8c213c2d00acd4486ab0a976e9e05ced28e1e4682cee1d7f9',
                                           'structure': {'keys': {'cwd': {'type': 'string'},
                                                                  'entrypoint': {'type': 'string'},
                                                                  'gitBranch': {'type': 'string'},
                                                                  'isSidechain': {'type': 'boolean'},
                                                                  'message': {'keys': {'content': {'items': [{'keys': {'content': {'items': [{'keys': {'text': {'type': 'string'},
                                                                                                                                                       'type': {'type': 'string'}},
                                                                                                                                              'type': 'object'}],
                                                                                                                                   'type': 'array'},
                                                                                                                       'tool_use_id': {'type': 'string'},
                                                                                                                       'type': {'type': 'string'}},
                                                                                                              'type': 'object'}],
                                                                                                   'type': 'array'},
                                                                                       'role': {'type': 'string'}},
                                                                              'type': 'object'},
                                                                  'parentUuid': {'type': 'string'},
                                                                  'permissionDecision': {'keys': {'decision': {'type': 'string'},
                                                                                                  'reasonType': {'type': 'string'},
                                                                                                  'source': {'type': 'string'}},
                                                                                         'type': 'object'},
                                                                  'promptId': {'type': 'string'},
                                                                  'sessionId': {'type': 'string'},
                                                                  'slug': {'type': 'string'},
                                                                  'sourceToolAssistantUUID': {'type': 'string'},
                                                                  'timestamp': {'type': 'string'},
                                                                  'toolUseResult': {'items': [{'keys': {'text': {'type': 'string'},
                                                                                                        'type': {'type': 'string'}},
                                                                                               'type': 'object'}],
                                                                                    'type': 'array'},
                                                                  'type': {'type': 'string'},
                                                                  'userType': {'type': 'string'},
                                                                  'uuid': {'type': 'string'},
                                                                  'version': {'type': 'string'}},
                                                         'type': 'object'}},
                                          {'sha256': 'c3ff66e369c0f29698f5dc0456b3ee7a6c010cbcf69840f5857671e9b39b9515',
                                           'structure': {'keys': {'advisorModel': {'type': 'string'},
                                                                  'apiBlockIndex': {'type': 'integer'},
                                                                  'cwd': {'type': 'string'},
                                                                  'effort': {'type': 'string'},
                                                                  'entrypoint': {'type': 'string'},
                                                                  'gitBranch': {'type': 'string'},
                                                                  'isSidechain': {'type': 'boolean'},
                                                                  'madeBy': {'type': 'string'},
                                                                  'message': {'keys': {'container': {'type': 'null'},
                                                                                       'content': {'items': [{'keys': {'caller': {'keys': {'type': {'type': 'string'}},
                                                                                                                                  'type': 'object'},
                                                                                                                       'id': {'type': 'string'},
                                                                                                                       'input': {'keys': {'session_id': {'type': 'string'}},
                                                                                                                                 'type': 'object'},
                                                                                                                       'name': {'type': 'string'},
                                                                                                                       'type': {'type': 'string'}},
                                                                                                              'type': 'object'}],
                                                                                                   'type': 'array'},
                                                                                       'context_management': {'type': 'null'},
                                                                                       'diagnostics': {'type': 'null'},
                                                                                       'id': {'type': 'string'},
                                                                                       'input_transformations': {'items': [],
                                                                                                                 'type': 'array'},
                                                                                       'model': {'type': 'string'},
                                                                                       'role': {'type': 'string'},
                                                                                       'stop_details': {'type': 'null'},
                                                                                       'stop_reason': {'type': 'string'},
                                                                                       'stop_sequence': {'type': 'null'},
                                                                                       'type': {'type': 'string'},
                                                                                       'usage': {'keys': {'cache_creation': {'keys': {'ephemeral_1h_input_tokens': {'type': 'integer'},
                                                                                                                                      'ephemeral_5m_input_tokens': {'type': 'integer'}},
                                                                                                                             'type': 'object'},
                                                                                                          'cache_creation_input_tokens': {'type': 'integer'},
                                                                                                          'cache_read_input_tokens': {'type': 'integer'},
                                                                                                          'fallback_credit': {'type': 'null'},
                                                                                                          'inference_geo': {'type': 'string'},
                                                                                                          'input_tokens': {'type': 'integer'},
                                                                                                          'iterations': {'items': [{'keys': {'cache_creation': {'keys': {'ephemeral_1h_input_tokens': {'type': 'integer'},
                                                                                                                                                                         'ephemeral_5m_input_tokens': {'type': 'integer'}},
                                                                                                                                                                'type': 'object'},
                                                                                                                                             'cache_creation_input_tokens': {'type': 'integer'},
                                                                                                                                             'cache_read_input_tokens': {'type': 'integer'},
                                                                                                                                             'input_tokens': {'type': 'integer'},
                                                                                                                                             'output_tokens': {'type': 'integer'},
                                                                                                                                             'type': {'type': 'string'}},
                                                                                                                                    'type': 'object'}],
                                                                                                                         'type': 'array'},
                                                                                                          'output_tokens': {'type': 'integer'},
                                                                                                          'output_tokens_details': {'keys': {'thinking_tokens': {'type': 'integer'}},
                                                                                                                                    'type': 'object'},
                                                                                                          'server_tool_use': {'keys': {'web_fetch_requests': {'type': 'integer'},
                                                                                                                                       'web_search_requests': {'type': 'integer'}},
                                                                                                                              'type': 'object'},
                                                                                                          'service_tier': {'type': 'string'},
                                                                                                          'speed': {'type': 'string'}},
                                                                                                 'type': 'object'}},
                                                                              'type': 'object'},
                                                                  'parentUuid': {'type': 'string'},
                                                                  'perTurnEffort': {'type': 'string'},
                                                                  'promptCacheRequested': {'type': 'string'},
                                                                  'requestId': {'type': 'string'},
                                                                  'requestedModel': {'type': 'string'},
                                                                  'serverClassifierRequest': {'type': 'string'},
                                                                  'sessionId': {'type': 'string'},
                                                                  'slug': {'type': 'string'},
                                                                  'timestamp': {'type': 'string'},
                                                                  'toolInputStreamMs': {'type': 'integer'},
                                                                  'type': {'type': 'string'},
                                                                  'userType': {'type': 'string'},
                                                                  'uuid': {'type': 'string'},
                                                                  'version': {'type': 'string'},
                                                                  'wireToolInputs': {'keys': {'fixture-tool-id': {'keys': {'session_id': {'type': 'string'}},
                                                                                                                  'type': 'object'}},
                                                                                     'type': 'object'}},
                                                         'type': 'object'}},
                                          {'sha256': '1aed63ac1ac025c27061702e94813c321bc8fe61a2d3fc609fe2a3d9d0f42640',
                                           'structure': {'keys': {'cwd': {'type': 'string'},
                                                                  'entrypoint': {'type': 'string'},
                                                                  'gitBranch': {'type': 'string'},
                                                                  'isSidechain': {'type': 'boolean'},
                                                                  'message': {'keys': {'content': {'items': [{'keys': {'content': {'items': [{'keys': {'text': {'type': 'string'},
                                                                                                                                                       'type': {'type': 'string'}},
                                                                                                                                              'type': 'object'}],
                                                                                                                                   'type': 'array'},
                                                                                                                       'tool_use_id': {'type': 'string'},
                                                                                                                       'type': {'type': 'string'}},
                                                                                                              'type': 'object'}],
                                                                                                   'type': 'array'},
                                                                                       'role': {'type': 'string'}},
                                                                              'type': 'object'},
                                                                  'parentUuid': {'type': 'string'},
                                                                  'permissionDecision': {'keys': {'decision': {'type': 'string'},
                                                                                                  'reasonType': {'type': 'string'},
                                                                                                  'source': {'type': 'string'}},
                                                                                         'type': 'object'},
                                                                  'promptId': {'type': 'string'},
                                                                  'sessionId': {'type': 'string'},
                                                                  'slug': {'type': 'string'},
                                                                  'sourceToolAssistantUUID': {'type': 'string'},
                                                                  'timestamp': {'type': 'string'},
                                                                  'toolUseResult': {'items': [{'keys': {'text': {'type': 'string'},
                                                                                                        'type': {'type': 'string'}},
                                                                                               'type': 'object'}],
                                                                                    'type': 'array'},
                                                                  'type': {'type': 'string'},
                                                                  'userType': {'type': 'string'},
                                                                  'uuid': {'type': 'string'},
                                                                  'version': {'type': 'string'}},
                                                         'type': 'object'}},
                                          {'sha256': 'c5bcd16c196e3b5f7eb1ca9886b38689bc472a2a69e30e774158afe73e514e17',
                                           'structure': {'keys': {'advisorModel': {'type': 'string'},
                                                                  'apiBlockIndex': {'type': 'integer'},
                                                                  'cwd': {'type': 'string'},
                                                                  'effort': {'type': 'string'},
                                                                  'entrypoint': {'type': 'string'},
                                                                  'gitBranch': {'type': 'string'},
                                                                  'isSidechain': {'type': 'boolean'},
                                                                  'madeBy': {'type': 'string'},
                                                                  'message': {'keys': {'container': {'type': 'null'},
                                                                                       'content': {'items': [{'keys': {'caller': {'keys': {'type': {'type': 'string'}},
                                                                                                                                  'type': 'object'},
                                                                                                                       'id': {'type': 'string'},
                                                                                                                       'input': {'keys': {'enabled': {'type': 'boolean'},
                                                                                                                                          'limit': {'type': 'integer'}},
                                                                                                                                 'type': 'object'},
                                                                                                                       'name': {'type': 'string'},
                                                                                                                       'type': {'type': 'string'}},
                                                                                                              'type': 'object'}],
                                                                                                   'type': 'array'},
                                                                                       'context_management': {'type': 'null'},
                                                                                       'diagnostics': {'keys': {'cache_miss_reason': {'keys': {'type': {'type': 'string'}},
                                                                                                                                      'type': 'object'}},
                                                                                                       'type': 'object'},
                                                                                       'id': {'type': 'string'},
                                                                                       'input_transformations': {'items': [{'keys': {'path': {'type': 'string'},
                                                                                                                                     'reason': {'type': 'string'},
                                                                                                                                     'type': {'type': 'string'}},
                                                                                                                            'type': 'object'},
                                                                                                                           {'keys': {'path': {'type': 'string'},
                                                                                                                                     'reason': {'type': 'string'},
                                                                                                                                     'type': {'type': 'string'}},
                                                                                                                            'type': 'object'}],
                                                                                                                 'type': 'array'},
                                                                                       'model': {'type': 'string'},
                                                                                       'role': {'type': 'string'},
                                                                                       'stop_details': {'type': 'null'},
                                                                                       'stop_reason': {'type': 'string'},
                                                                                       'stop_sequence': {'type': 'null'},
                                                                                       'type': {'type': 'string'},
                                                                                       'usage': {'keys': {'cache_creation': {'keys': {'ephemeral_1h_input_tokens': {'type': 'integer'},
                                                                                                                                      'ephemeral_5m_input_tokens': {'type': 'integer'}},
                                                                                                                             'type': 'object'},
                                                                                                          'cache_creation_input_tokens': {'type': 'integer'},
                                                                                                          'cache_read_input_tokens': {'type': 'integer'},
                                                                                                          'fallback_credit': {'type': 'null'},
                                                                                                          'inference_geo': {'type': 'string'},
                                                                                                          'input_tokens': {'type': 'integer'},
                                                                                                          'iterations': {'items': [{'keys': {'cache_creation': {'keys': {'ephemeral_1h_input_tokens': {'type': 'integer'},
                                                                                                                                                                         'ephemeral_5m_input_tokens': {'type': 'integer'}},
                                                                                                                                                                'type': 'object'},
                                                                                                                                             'cache_creation_input_tokens': {'type': 'integer'},
                                                                                                                                             'cache_read_input_tokens': {'type': 'integer'},
                                                                                                                                             'input_tokens': {'type': 'integer'},
                                                                                                                                             'output_tokens': {'type': 'integer'},
                                                                                                                                             'type': {'type': 'string'}},
                                                                                                                                    'type': 'object'}],
                                                                                                                         'type': 'array'},
                                                                                                          'output_tokens': {'type': 'integer'},
                                                                                                          'output_tokens_details': {'keys': {'thinking_tokens': {'type': 'integer'}},
                                                                                                                                    'type': 'object'},
                                                                                                          'server_tool_use': {'keys': {'web_fetch_requests': {'type': 'integer'},
                                                                                                                                       'web_search_requests': {'type': 'integer'}},
                                                                                                                              'type': 'object'},
                                                                                                          'service_tier': {'type': 'string'},
                                                                                                          'speed': {'type': 'string'}},
                                                                                                 'type': 'object'}},
                                                                              'type': 'object'},
                                                                  'parentUuid': {'type': 'string'},
                                                                  'perTurnEffort': {'type': 'string'},
                                                                  'promptCacheRequested': {'type': 'string'},
                                                                  'requestId': {'type': 'string'},
                                                                  'requestedModel': {'type': 'string'},
                                                                  'serverClassifierRequest': {'type': 'string'},
                                                                  'sessionId': {'type': 'string'},
                                                                  'slug': {'type': 'string'},
                                                                  'timestamp': {'type': 'string'},
                                                                  'toolInputStreamMs': {'type': 'integer'},
                                                                  'type': {'type': 'string'},
                                                                  'userType': {'type': 'string'},
                                                                  'uuid': {'type': 'string'},
                                                                  'version': {'type': 'string'},
                                                                  'wireToolInputs': {'keys': {'fixture-tool-id': {'keys': {'enabled': {'type': 'boolean'},
                                                                                                                           'limit': {'type': 'integer'}},
                                                                                                                  'type': 'object'}},
                                                                                     'type': 'object'}},
                                                         'type': 'object'}},
                                          {'sha256': '9510abd35a6049481ca7a208fc05e5ab26afc80df9b31e6e834a88de00839bbf',
                                           'structure': {'keys': {'cwd': {'type': 'string'},
                                                                  'entrypoint': {'type': 'string'},
                                                                  'gitBranch': {'type': 'string'},
                                                                  'isSidechain': {'type': 'boolean'},
                                                                  'message': {'keys': {'content': {'items': [{'keys': {'content': {'items': [{'keys': {'text': {'type': 'string'},
                                                                                                                                                       'type': {'type': 'string'}},
                                                                                                                                              'type': 'object'}],
                                                                                                                                   'type': 'array'},
                                                                                                                       'tool_use_id': {'type': 'string'},
                                                                                                                       'type': {'type': 'string'}},
                                                                                                              'type': 'object'}],
                                                                                                   'type': 'array'},
                                                                                       'role': {'type': 'string'}},
                                                                              'type': 'object'},
                                                                  'parentUuid': {'type': 'string'},
                                                                  'permissionDecision': {'keys': {'decision': {'type': 'string'},
                                                                                                  'reasonType': {'type': 'string'},
                                                                                                  'source': {'type': 'string'}},
                                                                                         'type': 'object'},
                                                                  'promptId': {'type': 'string'},
                                                                  'sessionId': {'type': 'string'},
                                                                  'slug': {'type': 'string'},
                                                                  'sourceToolAssistantUUID': {'type': 'string'},
                                                                  'timestamp': {'type': 'string'},
                                                                  'toolUseResult': {'items': [{'keys': {'text': {'type': 'string'},
                                                                                                        'type': {'type': 'string'}},
                                                                                               'type': 'object'}],
                                                                                    'type': 'array'},
                                                                  'type': {'type': 'string'},
                                                                  'userType': {'type': 'string'},
                                                                  'uuid': {'type': 'string'},
                                                                  'version': {'type': 'string'}},
                                                         'type': 'object'}}]}}
# END GENERATED SIGNATURE MANIFEST


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def blocks(row):
    message = row.get("message") or {}
    content = message.get("content", []) if isinstance(message, dict) else []
    return content if isinstance(content, list) else []


def scrub(value, key=None):
    """Replace local identity and private values while retaining real record shape."""
    if isinstance(value, list):
        return [scrub(item, key) for item in value]
    if isinstance(value, dict):
        result = {}
        for name, item in value.items():
            if name.startswith(("env_","cse_","toolu_")):
                name = "fixture-tool-id" if name.startswith("toolu_") else "fixture-id"
            if name in {"sessionId", "session_id"}:
                result[name] = LOCAL_SESSION
            elif name in {"uuid", "parentUuid", "promptId", "turnPromptId", "messageId", "requestId",
                          "deliveryId", "commandUuid", "id", "tool_use_id", "toolUseID", "sourceToolAssistantUUID"}:
                result[name] = "fixture-id"
            elif name in {"cwd", "path", "file_path", "command"}:
                result[name] = "/fixture/project" if name == "cwd" else "fixture operation"
            elif name in {"title", "project", "project_name"}:
                result[name] = "fixture project"
            elif name in {"signature", "thinking"}:
                result[name] = "fixture private content"
            elif name == "text":
                try:
                    payload = json.loads(item) if isinstance(item, str) else None
                except ValueError:
                    payload = None
                result[name] = (json.dumps(_mcp_payload(payload), ensure_ascii=False)
                                if payload is not None else "fixture private content")
            elif value.get("type") == "tool_result" and name == "content" and isinstance(item, str):
                result[name] = "fixture private content"
            else:
                result[name] = scrub(item, name)
        return result
    if isinstance(value, str):
        private_names={getpass.getuser(),Path.home().name}-{"", "."}; hostname=socket.gethostname()
        if private_names: value=re.sub(r"(?<![A-Za-z0-9_.-])(?:"+"|".join(map(re.escape,private_names))+r")(?![A-Za-z0-9_.-])","fixture-user",value)
        if hostname: value=value.replace(hostname,"fixture-host")
        value = re.sub(r"/Users/[^\s\"']+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", lambda m:"/fixture/user/path" if m[0].startswith("/Users/") else "fixture-email", value)
        value = re.sub(r"\b(?:env|cse|toolu)_[A-Za-z0-9_-]+\b", lambda m:"fixture-tool-id" if m[0].startswith("toolu_") else "fixture-id", value)
        value = re.sub(r"\b[A-Za-z0-9-]+/[A-Za-z0-9_.-]+\b", "fixture-owner/fixture-repo", value) if key=="remote" else value
        value = re.sub(r"github\.com[:/]([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+)", "github.com/fixture-owner/fixture-repo", value)
        value = re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "fixture-id", value)
        value = re.sub(r"session_[A-Za-z0-9_-]+", "session_fixture-lead", value)
        value = re.sub(r"(?i)work" + "less", "fixture-project", value)
        value = value.replace("teamwork-for-claude-code", "fixture-project").replace("tw/t71demo/demo", "fixture-branch")
        return value
    return value


def _private_content(value):
    """Scrub result payload values while preserving the observed JSON value shape."""
    if isinstance(value, dict):
        return {key: ("fixture private content" if key == "text" else _private_content(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_private_content(item) for item in value]
    if isinstance(value, str):
        return "fixture private content"
    return value


def _mcp_payload(value, key=None):
    """Sanitize parsed MCP JSON while retaining the result schema and useful status fields."""
    if isinstance(value, dict):
        result = {}
        for name, item in value.items():
            if name.startswith(("env_","cse_","toolu_")):
                name = "fixture-tool-id" if name.startswith("toolu_") else "fixture-id"
            if name in {"session_id", "sessionId", "uuid", "id", "tool_use_id"}:
                result[name] = "fixture-id"
            elif name in {"cwd", "path", "file_path"}:
                result[name] = "/fixture/project"
            elif name in {"title", "project", "project_name"}:
                result[name] = "fixture project"
            elif name in {"message", "prompt", "text"}:
                result[name] = "fixture private content"
            else:
                result[name] = _mcp_payload(item, name)
        return result
    if isinstance(value, list):
        return [_mcp_payload(item, key) for item in value]
    if isinstance(value, str):
        return scrub(value, key)
    return value


def _sanitize_mcp_result(row):
    for block in blocks(row):
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        content = block.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text" or not isinstance(item.get("text"), str):
                continue
            try:
                payload = json.loads(item["text"])
            except ValueError:
                item["text"] = "fixture private content"
            else:
                item["text"] = json.dumps(_mcp_payload(payload), ensure_ascii=False)

def dump(name, rows):
    path = OUT / name
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _shape(value):
    if isinstance(value, dict):
        return {"type": "object", "keys": {key: _shape(value[key]) for key in sorted(value)}}
    if isinstance(value, list):
        return {"type": "array", "items": [_shape(item) for item in value]}
    if value is None:
        kind = "null"
    elif isinstance(value, bool):
        kind = "boolean"
    elif isinstance(value, int):
        kind = "integer"
    elif isinstance(value, float):
        kind = "number"
    elif isinstance(value, str):
        kind = "string"
    else:
        kind = type(value).__name__
    return {"type": kind}


def fixture_signatures(path):
    """Recompute line count, per-line key/type structure, and content SHA-256."""
    lines = [line for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [json.loads(line) for line in lines]
    return {
        "line_count": len(lines),
        "rows": [
            {"structure": _shape(row), "sha256": hashlib.sha256(line.encode("utf-8")).hexdigest()}
            for row, line in zip(rows, lines)
        ],
    }


def _save_manifest(signatures):
    source = Path(__file__).read_text(encoding="utf-8")
    start = "# BEGIN GENERATED SIGNATURE MANIFEST\n"
    end = "# END GENERATED SIGNATURE MANIFEST"
    before, rest = source.split(start, 1)
    _, after = rest.split(end, 1)
    block = "SIGNATURE_MANIFEST = " + pformat(signatures, sort_dicts=True, width=110) + "\n"
    Path(__file__).write_text(before + start + block + end + after, encoding="utf-8")


def manifest_matches(name, path=None):
    """Compare a fixture (or a copied/tampered file) with its checked-in manifest entry."""
    fixture_path = Path(path) if path is not None else OUT / name
    return fixture_signatures(fixture_path) == SIGNATURE_MANIFEST.get(name)


def _has_dialogue_or_tool_content(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"message", "content", "text"} and child is not None:
                return True
            if key in {"tool_use", "tool_result"}:
                return True
            if _has_dialogue_or_tool_content(child):
                return True
    elif isinstance(value, list):
        return any(_has_dialogue_or_tool_content(item) for item in value)
    return False


def _quiz_rows(path):
    rows = read_rows(path)
    quiz_prompt = next((i for i, row in enumerate(rows) if row.get("type") == "user"
                        and "What is the current seat's branch name?" in json.dumps(row, ensure_ascii=False)), None)
    if quiz_prompt is None:
        raise ValueError("candidate source has no user QUIZ prompt")
    prompt = rows[quiz_prompt]
    question_text = json.dumps(prompt, ensure_ascii=False)
    answer_call = next((i for i in range(quiz_prompt + 1, len(rows))
                        if any("send_message" in str(block.get("name", "")) for block in blocks(rows[i])
                               if isinstance(block, dict) and block.get("type") == "tool_use")
                        and "QUIZ" in json.dumps(rows[i], ensure_ascii=False)), None)
    if answer_call is None:
        raise ValueError("candidate source has no send_message answer to its QUIZ")
    call_id = next(block.get("id") for block in blocks(rows[answer_call])
                   if isinstance(block, dict) and block.get("type") == "tool_use"
                   and "send_message" in str(block.get("name", "")))
    answer_result = next((i for i in range(answer_call + 1, len(rows))
                          if any(block.get("type") == "tool_result" and block.get("tool_use_id") == call_id
                                 for block in blocks(rows[i]) if isinstance(block, dict))), None)
    selected = [(quiz_prompt, prompt), (answer_call, rows[answer_call])]
    if answer_result is not None:
        selected.append((answer_result, rows[answer_result]))
    metadata = next((i for i, row in enumerate(rows)
                     if row.get("type") in {"file-history-snapshot", "file-history-delta"}
                     and not _has_dialogue_or_tool_content(row)), None)
    if metadata is None:
        metadata = next((i for i, row in enumerate(rows) if not _has_dialogue_or_tool_content(row)), None)
    if metadata is not None:
        selected.append((metadata, rows[metadata]))
    # Keep the source order and only records required to retain the observed quiz exchange.
    return [scrub(row) for _, row in sorted(selected)]


def _old_thread_rows(peer_path):
    peer_rows = read_rows(peer_path)
    indexed = {}
    peer_count = 0
    for index, row in enumerate(peer_rows):
        message = row.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if (peer_count < 2 and row.get("type") == "user" and isinstance(content, str)
                and "<cross-session-message" in content and "[tw:" in content):
            indexed[index] = row
            peer_count += 1
    bash_pair_added = False
    for index, row in enumerate(peer_rows):
        for block in blocks(row):
            if (not bash_pair_added and isinstance(block, dict) and block.get("type") == "tool_use"
                    and block.get("name") == "Bash"):
                tool_id = block.get("id")
                indexed[index] = row
                result_index = next((i for i in range(index + 1, len(peer_rows)) if any(
                    isinstance(candidate, dict) and candidate.get("type") == "tool_result"
                    and candidate.get("tool_use_id") == tool_id for candidate in blocks(peer_rows[i]))), None)
                if result_index is not None:
                    indexed[result_index] = peer_rows[result_index]
                bash_pair_added = result_index is not None
                break
    if not indexed:
        raise ValueError("record source has no cross-session rows or Bash tool result pair")
    return [scrub(indexed[i]) for i in sorted(indexed)]


def _tool_rows(path):
    rows = read_rows(path)
    calls = {}
    selected = []
    for row in rows:
        for block in blocks(row):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and any(kind in str(block.get("name", ""))
                    for kind in ("get_session", "list_events", "list_triggers")):
                calls[block.get("id")] = block.get("name")
                selected.append(row)
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                selected.append(row)
                del calls[block["tool_use_id"]]
    found = {kind for row in selected for block in blocks(row) if isinstance(block, dict)
             for kind in ("get_session", "list_events", "list_triggers") if kind in str(block.get("name", ""))}
    if not {"get_session", "list_events", "list_triggers"}.issubset(found):
        raise ValueError("tool source must contain get_session, list_events, and list_triggers")
    sanitized = [scrub(row) for row in selected]
    for row in sanitized:
        _sanitize_mcp_result(row)
    return sanitized

def _bridge_rows(path):
    rows = read_rows(path)
    hit = next((row for row in rows if re.fullmatch(r"[0-9a-fA-F-]{36}", str(row.get("session_id", "")))), None)
    if hit is None:
        raise ValueError("bridge source has no local UUID in session_id")
    first = scrub(rows[0]) if rows else {"type": "control_request"}
    first.pop("session_id", None)
    first.pop("sessionId", None)
    local = scrub(hit)
    local["session_id"] = LOCAL_SESSION
    return [first] + ([] if local == first else [local])


def main(candidate_source, bridge_source, tool_source, records_source):
    dump("t71_real_candidate.jsonl", _quiz_rows(candidate_source))
    dump("bridge-transcript-cse_fixture-remote-session.jsonl", _bridge_rows(bridge_source))
    dump("t71_real_tool_results.jsonl", _tool_rows(tool_source))
    dump("t71_old_thread.jsonl", _old_thread_rows(records_source))
    signatures = {name: fixture_signatures(OUT / name) for name in sorted(
        ("bridge-transcript-cse_fixture-remote-session.jsonl", "t71_real_candidate.jsonl",
         "t71_real_tool_results.jsonl", "t71_old_thread.jsonl"))}
    _save_manifest(signatures)
    print("Generated fixture signature manifest:")
    print(json.dumps(signatures, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit("usage: regenerate_t71.py REAL_CANDIDATE_JSONL REAL_BRIDGE_JSONL REAL_TOOL_RESULTS_JSONL REAL_RECORDS_JSONL")
    main(*sys.argv[1:])
