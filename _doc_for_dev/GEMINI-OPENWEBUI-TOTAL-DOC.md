[ DOCUMENTATION API GEMINI et Open-WEBUI]

> [!NOTE]
> **Note:** If you use the official [Google Gen AI SDKs](https://ai.google.dev/gemini-api/docs/libraries) and use the chat feature (or append the full model response object directly to history), **thought signatures are handled automatically**. You do not need to manually extract or manage them, or change your code.

Thought signatures are encrypted representations of the model's internal thought
process and are used to preserve reasoning context across multi-step
interactions.
When using thinking models (such as the Gemini 3 and 2.5 series), the API may
return a `thoughtSignature` field within the [content parts](https://ai.google.dev/api/caching#Part)
of the response (e.g., `text` or `functionCall` parts).

As a general rule, if you receive a thought signature in a model response,
you should pass it back exactly as received when sending the conversation
history in the next turn.
**When using Gemini 3 models, you must pass back thought signatures during
function calling, otherwise you will get a validation error** (4xx status code).
This includes when using the `minimal`
[thinking level](https://ai.google.dev/gemini-api/docs/thinking#thinking-levels) setting for Gemini 3
Flash.

## How it works

The graphic below visualizes the meaning of "turn" and "step" as they pertain to
[function calling](https://ai.google.dev/gemini-api/docs/function-calling) in the Gemini API. A "turn"
is a single, complete exchange in a conversation between a user and a model. A
"step" is a finer-grained action or operation performed by the model, often as
part of a larger process to complete a turn.

![Function calling turns and steps diagram](https://ai.google.dev/static/gemini-api/docs/images/fc-turns.png)

*This document focuses on handling function calling for Gemini 3 models. Refer
to the [model behavior](https://ai.google.dev/gemini-api/docs/thought-signatures#model-behavior) section for discrepancies with 2.5.*

Gemini 3 returns thought signatures for all model responses (responses from
the API) with a function call. Thought signatures show up in the following
cases:

- When there are [parallel function](https://ai.google.dev/gemini-api/docs/function-calling#parallel_function_calling) calls, the first function call part returned by the model response will have a thought signature.
- When there are sequential function calls (multi-step), each function call will have a signature and you must pass all signatures back.
- Model responses without a function call will return a thought signature inside the last part returned by the model.

The following table provides a visualization for multi-step function calls,
combining the definitions of turns and steps with the concept of signatures
introduced above:

|---|---|---|---|---|
| **Turn** | **Step** | **User Request** | **Model Response** | **FunctionResponse** |
| 1 | 1 | `request1 = user_prompt` | `FC1 + signature` | `FR1` |
| 1 | 2 | `request2 = request1 + (FC1 + signature) + FR1` | `FC2 + signature` | `FR2` |
| 1 | 3 | `request3 = request2 + (FC2 + signature) + FR2` | `text_output <br /> ` `(no FCs)` | None |

## Signatures in function calling parts

When Gemini generates a `functionCall`, it relies on the `thought_signature`
to process the tool's output correctly in the next turn.

- **Behavior** :
  - **Single Function Call** : The `functionCall` part will contain a `thought_signature`.
  - **Parallel Function Calls** : If the model generates parallel function calls in a response, the `thought_signature` is attached **only to the first** `functionCall` part. Subsequent `functionCall` parts in the same response will **not** contain a signature.
- **Requirement** : You **must** return this signature in the exact part where it was received when sending the conversation history back.
- **Validation** : Strict validation is enforced for all function calls within the current turn . (Only current turn is required; we don't validate on previous turns)
  - The API goes back in the history (newest to oldest) to find the most recent **User** message that contains standard content (e.g., `text`) ( which would be the start of the current turn). This will not **be** a `functionResponse`.
  - **All** model `functionCall` turns occurring after that specific use message are considered part of the turn.
  - The **first** `functionCall` part in **each step** of the current turn **must** include its `thought_signature`.
  - If you omit a `thought_signature` for the first `functionCall` part in any step of the current turn, the request will fail with a 400 error.
- **If proper signatures are not returned, here is how you will error out**
  - Gemini 3 models: Failure to include signatures will result in a 400 error. The verbiage will be of the form:
    - Function call `<Function Call>` in the `<index of contents array>` content block is missing a `thought_signature`. For example, *Function
      call `FC1` in the `1.` content block is missing a `thought_signature`.*

### Sequential function calling example

This section shows an example of multiple function calls where the user asks a
complex question requiring multiple tasks.

Let's walk through a multiple-turn function calling example where the user asks
a complex question requiring multiple tasks: `"Check flight status for AA100 and
book a taxi if delayed"`.

|---|---|---|---|---|
| **Turn** | **Step** | **User Request** | **Model Response** | **FunctionResponse** |
| 1 | 1 | `request1="Check flight status for AA100 and book a taxi 2 hours before if delayed."` | `FC1 ("check_flight") + signature` | `FR1` |
| 1 | 2 | `request2 = request1 + FC1 ("check_flight") + signature + FR1` | `FC2("book_taxi") + signature` | `FR2` |
| 1 | 3 | `request3 = request2 + FC2 ("book_taxi") + signature + FR2` | `text_output <br /> ` `(no FCs)` | `None` |

The following code illustrates the sequence in the above table.

**Turn 1, Step 1 (User request)**

    {
      "contents": [
        {
          "role": "user",
          "parts": [
            {
              "text": "Check flight status for AA100 and book a taxi 2 hours before if delayed."
            }
          ]
        }
      ],
      "tools": [
        {
          "functionDeclarations": [
            {
              "name": "check_flight",
              "description": "Gets the current status of a flight",
              "parameters": {
                "type": "object",
                "properties": {
                  "flight": {
                    "type": "string",
                    "description": "The flight number to check"
                  }
                },
                "required": [
                  "flight"
                ]
              }
            },
            {
              "name": "book_taxi",
              "description": "Book a taxi",
              "parameters": {
                "type": "object",
                "properties": {
                  "time": {
                    "type": "string",
                    "description": "time to book the taxi"
                  }
                },
                "required": [
                  "time"
                ]
              }
            }
          ]
        }
      ]
    }

**Turn 1, Step 1 (Model response)**

    {
    "content": {
            "role": "model",
            "parts": [
              {
                "functionCall": {
                  "name": "check_flight",
                  "args": {
                    "flight": "AA100"
                  }
                },
                "thoughtSignature": "<Signature A>"
              }
            ]
      }
    }

**Turn 1, Step 2 (User response - Sending tool outputs)** Since this user turn
only contains a `functionResponse` (no fresh text), we are still in Turn 1. We
must preserve `<Signature_A>`.

    {
          "role": "user",
          "parts": [
            {
              "text": "Check flight status for AA100 and book a taxi 2 hours before if delayed."
            }
          ]
        },
        {
            "role": "model",
            "parts": [
              {
                "functionCall": {
                  "name": "check_flight",
                  "args": {
                    "flight": "AA100"
                  }
                },
                "thoughtSignature": "<Signature A>" //Required and Validated
              }
            ]
          },
          {
            "role": "user",
            "parts": [
              {
                "functionResponse": {
                  "name": "check_flight",
                  "response": {
                    "status": "delayed",
                    "departure_time": "12 PM"
                    }
                  }
                }
            ]
    }

**Turn 1, Step 2 (Model)** The model now decides to book a taxi based on the
previous tool output.

    {
          "content": {
            "role": "model",
            "parts": [
              {
                "functionCall": {
                  "name": "book_taxi",
                  "args": {
                    "time": "10 AM"
                  }
                },
                "thoughtSignature": "<Signature B>"
              }
            ]
          }
    }

**Turn 1, Step 3 (User - Sending tool output)** To send the taxi booking
confirmation, we must include signatures for **ALL** function calls in this loop
(`<Signature A>` + `<Signature B>`).

    {
          "role": "user",
          "parts": [
            {
              "text": "Check flight status for AA100 and book a taxi 2 hours before if delayed."
            }
          ]
        },
        {
            "role": "model",
            "parts": [
              {
                "functionCall": {
                  "name": "check_flight",
                  "args": {
                    "flight": "AA100"
                  }
                },
                "thoughtSignature": "<Signature A>" //Required and Validated
              }
            ]
          },
          {
            "role": "user",
            "parts": [
              {
                "functionResponse": {
                  "name": "check_flight",
                  "response": {
                    "status": "delayed",
                    "departure_time": "12 PM"
                  }
                  }
                }
            ]
          },
          {
            "role": "model",
            "parts": [
              {
                "functionCall": {
                  "name": "book_taxi",
                  "args": {
                    "time": "10 AM"
                  }
                },
                "thoughtSignature": "<Signature B>" //Required and Validated
              }
            ]
          },
          {
            "role": "user",
            "parts": [
              {
                "functionResponse": {
                  "name": "book_taxi",
                  "response": {
                    "booking_status": "success"
                  }
                  }
                }
            ]
        }
    }

### Parallel function calling example

Let's walk through a parallel function calling example where the users asks
`"Check weather in Paris and London"` to see where the model does validation.

| **Turn** | **Step** | **User Request** | **Model Response** | **FunctionResponse** |
|---|---|---|---|---|
| 1 | 1 | request1="Check the weather in Paris and London" | FC1 ("Paris") + signature FC2 ("London") | FR1 |
| 1 | 2 | request 2 **=** request1 **+** FC1 ("Paris") + signature + FC2 ("London") | text_output (no FCs) | None |

The following code illustrates the sequence in the above table.

**Turn 1, Step 1 (User request)**

    {
      "contents": [
        {
          "role": "user",
          "parts": [
            {
              "text": "Check the weather in Paris and London."
            }
          ]
        }
      ],
      "tools": [
        {
          "functionDeclarations": [
            {
              "name": "get_current_temperature",
              "description": "Gets the current temperature for a given location.",
              "parameters": {
                "type": "object",
                "properties": {
                  "location": {
                    "type": "string",
                    "description": "The city name, e.g. San Francisco"
                  }
                },
                "required": [
                  "location"
                ]
              }
            }
          ]
        }
      ]
    }

**Turn 1, Step 1 (Model response)**

    {
      "content": {
        "parts": [
          {
            "functionCall": {
              "name": "get_current_temperature",
              "args": {
                "location": "Paris"
              }
            },
            "thoughtSignature": "<Signature_A>"// INCLUDED on First FC
          },
          {
            "functionCall": {
              "name": "get_current_temperature",
              "args": {
                "location": "London"
              }// NO signature on subsequent parallel FCs
            }
          }
        ]
      }
    }

**Turn 1, Step 2 (User response - Sending tool outputs)** We must preserve
`<Signature_A>` on the first part exactly as received.

    [
      {
        "role": "user",
        "parts": [
          {
            "text": "Check the weather in Paris and London."
          }
        ]
      },
      {
        "role": "model",
        "parts": [
          {
            "functionCall": {
              "name": "get_current_temperature",
              "args": {
                "city": "Paris"
              }
            },
            "thought_signature": "<Signature_A>" // MUST BE INCLUDED
          },
          {
            "functionCall": {
              "name": "get_current_temperature",
              "args": {
                "city": "London"
              }
            }
          } // NO SIGNATURE FIELD
        ]
      },
      {
        "role": "user",
        "parts": [
          {
            "functionResponse": {
              "name": "get_current_temperature",
              "response": {
                "temp": "15C"
              }
            }
          },
          {
            "functionResponse": {
              "name": "get_current_temperature",
              "response": {
                "temp": "12C"
              }
            }
          }
        ]
      }
    ]

## Signatures in non `functionCall` parts

Gemini may also return `thought_signatures` in the final part of the response
in non-function-call parts.

- **Behavior** : The final content part (`text, inlineData...`) returned by the model may contain a `thought_signature`.
- **Recommendation** : Returning these signatures is **recommended** to ensure the model maintains high-quality reasoning, especially for complex instruction following or simulated agentic workflows.
- **Validation** : The API does **not** strictly enforce validation. You won't receive a blocking error if you omit them, though performance may degrade.

### Text/In-context reasoning (No validation)

**Turn 1, Step 1 (Model response)**

    {
      "role": "model",
      "parts": [
        {
          "text": "I need to calculate the risk. Let me think step-by-step...",
          "thought_signature": "<Signature_C>" // OPTIONAL (Recommended)
        }
      ]
    }

**Turn 2, Step 1 (User)**

    [
      { "role": "user", "parts": [{ "text": "What is the risk?" }] },
      {
        "role": "model", 
        "parts": [
          {
            "text": "I need to calculate the risk. Let me think step-by-step...",
            // If you omit <Signature_C> here, no error will occur.
          }
        ]
      },
      { "role": "user", "parts": [{ "text": "Summarize it." }] }
    ]

## Signatures for OpenAI compatibility

The following examples shows how to handle thought signatures for a chat
completion API using [OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai).

### Sequential function calling example

This is an example of multiple function calling where the user asks a complex
question requiring multiple tasks.

Let's walk through a multiple-turn function calling example where the user asks
`Check flight status for AA100 and book a taxi if delayed` and you can see what
happens when the user asks a complex question requiring multiple tasks.

|---|---|---|---|---|
| **Turn** | **Step** | **User Request** | **Model Response** | **FunctionResponse** |
| 1 | 1 | `request1="Check the weather in Paris and London"` | `FC1 ("Paris") + signature <br /> ` `FC2 ("London")` | `FR1` |
| 1 | 2 | `request 2 = request1 + FC1 ("Paris") + signature + FC2 ("London")` | `text_output <br /> ` `(no FCs)` | `None` |

The following code walks through the given sequence.

**Turn 1, Step 1 (User Request)**

    {
      "model": "google/gemini-3.1-pro-preview",
      "messages": [
        {
          "role": "user",
          "content": "Check flight status for AA100 and book a taxi 2 hours before if delayed."
        }
      ],
      "tools": [
        {
          "type": "function",
          "function": {
            "name": "check_flight",
            "description": "Gets the current status of a flight",
            "parameters": {
              "type": "object",
              "properties": {
                "flight": {
                  "type": "string",
                  "description": "The flight number to check."
                }
              },
              "required": [
                "flight"
              ]
            }
          }
        },
        {
          "type": "function",
          "function": {
            "name": "book_taxi",
            "description": "Book a taxi",
            "parameters": {
              "type": "object",
              "properties": {
                "time": {
                  "type": "string",
                  "description": "time to book the taxi"
                }
              },
              "required": [
                "time"
              ]
            }
          }
        }
      ]
    }

**Turn 1, Step 1 (Model Response)**

    {
          "role": "model",
            "tool_calls": [
              {
                "extra_content": {
                  "google": {
                    "thought_signature": "<Signature A>"
                  }
                },
                "function": {
                  "arguments": "{\"flight\":\"AA100\"}",
                  "name": "check_flight"
                },
                "id": "function-call-1",
                "type": "function"
              }
            ]
        }

**Turn 1, Step 2 (User Response - Sending Tool Outputs)**

Since this user turn only contains a `functionResponse` (no fresh text), we are
still in Turn 1 and must preserve `<Signature_A>`.

    "messages": [
        {
          "role": "user",
          "content": "Check flight status for AA100 and book a taxi 2 hours before if delayed."
        },
        {
          "role": "model",
            "tool_calls": [
              {
                "extra_content": {
                  "google": {
                    "thought_signature": "<Signature A>" //Required and Validated
                  }
                },
                "function": {
                  "arguments": "{\"flight\":\"AA100\"}",
                  "name": "check_flight"
                },
                "id": "function-call-1",
                "type": "function"
              }
            ]
        },
        {
          "role": "tool",
          "name": "check_flight",
          "tool_call_id": "function-call-1",
          "content": "{\"status\":\"delayed\",\"departure_time\":\"12 PM\"}"                 
        }
      ]

**Turn 1, Step 2 (Model)**

The model now decides to book a taxi based on the previous tool output.

    {
    "role": "model",
    "tool_calls": [
    {
    "extra_content": {
    "google": {
    "thought_signature": "<Signature B>"
    }
                },
                "function": {
                  "arguments": "{\"time\":\"10 AM\"}",
                  "name": "book_taxi"
                },
                "id": "function-call-2",
                "type": "function"
              }
           ]
    }

**Turn 1, Step 3 (User - Sending Tool Output)**

To send the taxi booking confirmation, we must include signatures for ALL
function calls in this loop (`<Signature A>` + `<Signature B>`).

    "messages": [
        {
          "role": "user",
          "content": "Check flight status for AA100 and book a taxi 2 hours before if delayed."
        },
        {
          "role": "model",
            "tool_calls": [
              {
                "extra_content": {
                  "google": {
                    "thought_signature": "<Signature A>" //Required and Validated
                  }
                },
                "function": {
                  "arguments": "{\"flight\":\"AA100\"}",
                  "name": "check_flight"
                },
                "id": "function-call-1d6a1a61-6f4f-4029-80ce-61586bd86da5",
                "type": "function"
              }
            ]
        },
        {
          "role": "tool",
          "name": "check_flight",
          "tool_call_id": "function-call-1d6a1a61-6f4f-4029-80ce-61586bd86da5",
          "content": "{\"status\":\"delayed\",\"departure_time\":\"12 PM\"}"                 
        },
        {
          "role": "model",
            "tool_calls": [
              {
                "extra_content": {
                  "google": {
                    "thought_signature": "<Signature B>" //Required and Validated
                  }
                },
                "function": {
                  "arguments": "{\"time\":\"10 AM\"}",
                  "name": "book_taxi"
                },
                "id": "function-call-65b325ba-9b40-4003-9535-8c7137b35634",
                "type": "function"
              }
            ]
        },
        {
          "role": "tool",
          "name": "book_taxi",
          "tool_call_id": "function-call-65b325ba-9b40-4003-9535-8c7137b35634",
          "content": "{\"booking_status\":\"success\"}"
        }
      ]

### Parallel function calling example

Let's walk through a parallel function calling example where the users asks
`"Check weather in Paris and London"` and you can see where the model does
validation.

|---|---|---|---|---|
| **Turn** | **Step** | **User Request** | **Model Response** | **FunctionResponse** |
| 1 | 1 | `request1="Check the weather in Paris and London"` | `FC1 ("Paris") + signature <br /> ` `FC2 ("London")` | `FR1` |
| 1 | 2 | `request 2 = request1 + FC1 ("Paris") + signature + FC2 ("London")` | `text_output <br /> ` `(no FCs)` | `None` |

Here's the code to walk through the given sequence.

**Turn 1, Step 1 (User Request)**

    {
      "contents": [
        {
          "role": "user",
          "parts": [
            {
              "text": "Check the weather in Paris and London."
            }
          ]
        }
      ],
      "tools": [
        {
          "functionDeclarations": [
            {
              "name": "get_current_temperature",
              "description": "Gets the current temperature for a given location.",
              "parameters": {
                "type": "object",
                "properties": {
                  "location": {
                    "type": "string",
                    "description": "The city name, e.g. San Francisco"
                  }
                },
                "required": [
                  "location"
                ]
              }
            }
          ]
        }
      ]
    }

**Turn 1, Step 1 (Model Response)**

    {
    "role": "assistant",
            "tool_calls": [
              {
                "extra_content": {
                  "google": {
                    "thought_signature": "<Signature A>" //Signature returned
                  }
                },
                "function": {
                  "arguments": "{\"location\":\"Paris\"}",
                  "name": "get_current_temperature"
                },
                "id": "function-call-f3b9ecb3-d55f-4076-98c8-b13e9d1c0e01",
                "type": "function"
              },
              {
                "function": {
                  "arguments": "{\"location\":\"London\"}",
                  "name": "get_current_temperature"
                },
                "id": "function-call-335673ad-913e-42d1-bbf5-387c8ab80f44",
                "type": "function" // No signature on Parallel FC
              }
            ]
    }

**Turn 1, Step 2 (User Response - Sending Tool Outputs)**

You must preserve `<Signature_A>` on the first part exactly as received.

    "messages": [
        {
          "role": "user",
          "content": "Check the weather in Paris and London."
        },
        {
          "role": "assistant",
            "tool_calls": [
              {
                "extra_content": {
                  "google": {
                    "thought_signature": "<Signature A>" //Required
                  }
                },
                "function": {
                  "arguments": "{\"location\":\"Paris\"}",
                  "name": "get_current_temperature"
                },
                "id": "function-call-f3b9ecb3-d55f-4076-98c8-b13e9d1c0e01",
                "type": "function"
              },
              {
                "function": { //No Signature
                  "arguments": "{\"location\":\"London\"}",
                  "name": "get_current_temperature"
                },
                "id": "function-call-335673ad-913e-42d1-bbf5-387c8ab80f44",
                "type": "function"
              }
            ]
        },
        {
          "role":"tool",
          "name": "get_current_temperature",
          "tool_call_id": "function-call-f3b9ecb3-d55f-4076-98c8-b13e9d1c0e01",
          "content": "{\"temp\":\"15C\"}"
        },    
        {
          "role":"tool",
          "name": "get_current_temperature",
          "tool_call_id": "function-call-335673ad-913e-42d1-bbf5-387c8ab80f44",
          "content": "{\"temp\":\"12C\"}"
        }
      ]

## FAQs

1. **How do I transfer history from a different model to Gemini 3 with a
   function call part in the current turn and step? I need to provide function call
   parts that were not generated by the API and therefore don't have an associated
   thought signature?**

   While injecting custom function call blocks into the request is strongly
   discouraged, in cases where it can't be avoided, e.g. providing information
   to the model on function calls and responses that were executed
   deterministically by the client, or transferring a trace from a different
   model that does not include thought signatures, you can set the following
   dummy signatures of either `"context_engineering_is_the_way_to_go"` or
   `"skip_thought_signature_validator"` in the thought signature field to skip
   validation.
2. **I am sending back interleaved parallel function calls and responses and the
   API is returning a 400. Why?**

   When the API returns parallel function calls "FC1 + signature, FC2", the
   user response expected is "FC1+ signature, FC2, FR1, FR2". If you have them
   interleaved as "FC1 + signature, FR1, FC2, FR2" the API will return a 400
   error.
3. **When streaming and the model is not returning a function call I can't find
   the thought signature**

   During a model response not containing a FC with a streaming request, the
   model may return the thought signature in a part with an empty text content
   part. It is advisable to parse the entire request until the `finish_reason`
   is returned by the model.

## Thought signatures for different models

[Gemini 3 models](https://ai.google.dev/gemini-api/docs/models#gemini-3) and Gemini 2.5 models
behave differently with thought signatures in function calls:

- If there are function calls in a response,
  - Gemini 3 will always have the signature on the first function call part. It is **mandatory** to return that part.
  - Gemini 2.5 will have the signature in the first part (regardless of type). It is **optional** to return that part.
- If there are no function calls in a response,
  - Gemini 3 will have the signature on the last part if the model generates a thought.
  - Gemini 2.5 won't have a signature in any part.

Refer to the [Thinking](https://ai.google.dev/gemini-api/docs/thinking#signatures) page for more
comparison details.
For Gemini 3 Image models see the thinking process section of the
[Image generation](https://ai.google.dev/gemini-api/docs/image-generation#thinking-process) guide.


You can configure Gemini models to generate responses that adhere to a provided JSON
Schema. This ensures predictable, type-safe results and simplifies extracting
structured data from unstructured text.

Using structured outputs is ideal for:

- **Data extraction:** Pull specific information like names and dates from text.
- **Structured classification:** Classify text into predefined categories.
- **Agentic workflows:** Generate structured inputs for tools or APIs.

In addition to supporting JSON Schema in the REST API, the Google GenAI SDKs
make it easy to define schemas using
[Pydantic](https://docs.pydantic.dev/latest/) (Python) and
[Zod](https://zod.dev/) (JavaScript).

<button value="recipe" default="">Recipe Extractor</button> <button value="feedback">Content Moderation</button> <button value="recursive">Recursive Structures</button>

This example demonstrates how to extract structured data from text using basic
JSON Schema types like `object`, `array`, `string`, and `integer`.

### Python

    from google import genai
    from pydantic import BaseModel, Field
    from typing import List, Optional

    class Ingredient(BaseModel):
        name: str = Field(description="Name of the ingredient.")
        quantity: str = Field(description="Quantity of the ingredient, including units.")

    class Recipe(BaseModel):
        recipe_name: str = Field(description="The name of the recipe.")
        prep_time_minutes: Optional[int] = Field(description="Optional time in minutes to prepare the recipe.")
        ingredients: List[Ingredient]
        instructions: List[str]

    client = genai.Client()

    prompt = """
    Please extract the recipe from the following text.
    The user wants to make delicious chocolate chip cookies.
    They need 2 and 1/4 cups of all-purpose flour, 1 teaspoon of baking soda,
    1 teaspoon of salt, 1 cup of unsalted butter (softened), 3/4 cup of granulated sugar,
    3/4 cup of packed brown sugar, 1 teaspoon of vanilla extract, and 2 large eggs.
    For the best part, they'll need 2 cups of semisweet chocolate chips.
    First, preheat the oven to 375°F (190°C). Then, in a small bowl, whisk together the flour,
    baking soda, and salt. In a large bowl, cream together the butter, granulated sugar, and brown sugar
    until light and fluffy. Beat in the vanilla and eggs, one at a time. Gradually beat in the dry
    ingredients until just combined. Finally, stir in the chocolate chips. Drop by rounded tablespoons
    onto ungreased baking sheets and bake for 9 to 11 minutes.
    """

    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_json_schema": Recipe.model_json_schema(),
        },
    )

    recipe = Recipe.model_validate_json(response.text)
    print(recipe)

### JavaScript

    import { GoogleGenAI } from "@google/genai";
    import { z } from "zod";
    import { zodToJsonSchema } from "zod-to-json-schema";

    const ingredientSchema = z.object({
      name: z.string().describe("Name of the ingredient."),
      quantity: z.string().describe("Quantity of the ingredient, including units."),
    });

    const recipeSchema = z.object({
      recipe_name: z.string().describe("The name of the recipe."),
      prep_time_minutes: z.number().optional().describe("Optional time in minutes to prepare the recipe."),
      ingredients: z.array(ingredientSchema),
      instructions: z.array(z.string()),
    });

    const ai = new GoogleGenAI({});

    const prompt = `
    Please extract the recipe from the following text.
    The user wants to make delicious chocolate chip cookies.
    They need 2 and 1/4 cups of all-purpose flour, 1 teaspoon of baking soda,
    1 teaspoon of salt, 1 cup of unsalted butter (softened), 3/4 cup of granulated sugar,
    3/4 cup of packed brown sugar, 1 teaspoon of vanilla extract, and 2 large eggs.
    For the best part, they'll need 2 cups of semisweet chocolate chips.
    First, preheat the oven to 375°F (190°C). Then, in a small bowl, whisk together the flour,
    baking soda, and salt. In a large bowl, cream together the butter, granulated sugar, and brown sugar
    until light and fluffy. Beat in the vanilla and eggs, one at a time. Gradually beat in the dry
    ingredients until just combined. Finally, stir in the chocolate chips. Drop by rounded tablespoons
    onto ungreased baking sheets and bake for 9 to 11 minutes.
    `;

    const response = await ai.models.generateContent({
      model: "gemini-3-flash-preview",
      contents: prompt,
      config: {
        responseMimeType: "application/json",
        responseJsonSchema: zodToJsonSchema(recipeSchema),
      },
    });

    const recipe = recipeSchema.parse(JSON.parse(response.text));
    console.log(recipe);

### Go

    package main

    import (
        "context"
        "fmt"
        "log"

        "google.golang.org/genai"
    )

    func main() {
        ctx := context.Background()
        client, err := genai.NewClient(ctx, nil)
        if err != nil {
            log.Fatal(err)
        }

        prompt := `
      Please extract the recipe from the following text.
      The user wants to make delicious chocolate chip cookies.
      They need 2 and 1/4 cups of all-purpose flour, 1 teaspoon of baking soda,
      1 teaspoon of salt, 1 cup of unsalted butter (softened), 3/4 cup of granulated sugar,
      3/4 cup of packed brown sugar, 1 teaspoon of vanilla extract, and 2 large eggs.
      For the best part, they'll need 2 cups of semisweet chocolate chips.
      First, preheat the oven to 375°F (190°C). Then, in a small bowl, whisk together the flour,
      baking soda, and salt. In a large bowl, cream together the butter, granulated sugar, and brown sugar
      until light and fluffy. Beat in the vanilla and eggs, one at a time. Gradually beat in the dry
      ingredients until just combined. Finally, stir in the chocolate chips. Drop by rounded tablespoons
      onto ungreased baking sheets and bake for 9 to 11 minutes.
      `
        config := &genai.GenerateContentConfig{
            ResponseMIMEType: "application/json",
            ResponseJsonSchema: map[string]any{
                "type": "object",
                "properties": map[string]any{
                    "recipe_name": map[string]any{
                        "type":        "string",
                        "description": "The name of the recipe.",
                    },
                    "prep_time_minutes": map[string]any{
                        "type":        "integer",
                        "description": "Optional time in minutes to prepare the recipe.",
                    },
                    "ingredients": map[string]any{
                        "type": "array",
                        "items": map[string]any{
                            "type": "object",
                            "properties": map[string]any{
                                "name": map[string]any{
                                    "type":        "string",
                                    "description": "Name of the ingredient.",
                                },
                                "quantity": map[string]any{
                                    "type":        "string",
                                    "description": "Quantity of the ingredient, including units.",
                                },
                            },
                            "required": []string{"name", "quantity"},
                        },
                    },
                    "instructions": map[string]any{
                        "type":  "array",
                        "items": map[string]any{"type": "string"},
                    },
                },
                "required": []string{"recipe_name", "ingredients", "instructions"},
            },
        }

        result, err := client.Models.GenerateContent(
            ctx,
            "gemini-3-flash-preview",
            genai.Text(prompt),
            config,
        )
        if err != nil {
            log.Fatal(err)
        }
        fmt.Println(result.Text())
    }

### REST

    curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent" \
        -H "x-goog-api-key: $GEMINI_API_KEY" \
        -H 'Content-Type: application/json' \
        -X POST \
        -d '{
          "contents": [{
            "parts":[
              { "text": "Please extract the recipe from the following text.\nThe user wants to make delicious chocolate chip cookies.\nThey need 2 and 1/4 cups of all-purpose flour, 1 teaspoon of baking soda,\n1 teaspoon of salt, 1 cup of unsalted butter (softened), 3/4 cup of granulated sugar,\n3/4 cup of packed brown sugar, 1 teaspoon of vanilla extract, and 2 large eggs.\nFor the best part, they will need 2 cups of semisweet chocolate chips.\nFirst, preheat the oven to 375°F (190°C). Then, in a small bowl, whisk together the flour,\nbaking soda, and salt. In a large bowl, cream together the butter, granulated sugar, and brown sugar\nuntil light and fluffy. Beat in the vanilla and eggs, one at a time. Gradually beat in the dry\ningredients until just combined. Finally, stir in the chocolate chips. Drop by rounded tablespoons\nonto ungreased baking sheets and bake for 9 to 11 minutes." }
            ]
          }],
          "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": {
              "type": "object",
              "properties": {
                "recipe_name": {
                  "type": "string",
                  "description": "The name of the recipe."
                },
                "prep_time_minutes": {
                    "type": "integer",
                    "description": "Optional time in minutes to prepare the recipe."
                },
                "ingredients": {
                  "type": "array",
                  "items": {
                    "type": "object",
                    "properties": {
                      "name": { "type": "string", "description": "Name of the ingredient."},
                      "quantity": { "type": "string", "description": "Quantity of the ingredient, including units."}
                    },
                    "required": ["name", "quantity"]
                  }
                },
                "instructions": {
                  "type": "array",
                  "items": { "type": "string" }
                }
              },
              "required": ["recipe_name", "ingredients", "instructions"]
            }
          }
        }'

**Example Response:**

    {
      "recipe_name": "Delicious Chocolate Chip Cookies",
      "ingredients": [
        {
          "name": "all-purpose flour",
          "quantity": "2 and 1/4 cups"
        },
        {
          "name": "baking soda",
          "quantity": "1 teaspoon"
        },
        {
          "name": "salt",
          "quantity": "1 teaspoon"
        },
        {
          "name": "unsalted butter (softened)",
          "quantity": "1 cup"
        },
        {
          "name": "granulated sugar",
          "quantity": "3/4 cup"
        },
        {
          "name": "packed brown sugar",
          "quantity": "3/4 cup"
        },
        {
          "name": "vanilla extract",
          "quantity": "1 teaspoon"
        },
        {
          "name": "large eggs",
          "quantity": "2"
        },
        {
          "name": "semisweet chocolate chips",
          "quantity": "2 cups"
        }
      ],
      "instructions": [
        "Preheat the oven to 375°F (190°C).",
        "In a small bowl, whisk together the flour, baking soda, and salt.",
        "In a large bowl, cream together the butter, granulated sugar, and brown sugar until light and fluffy.",
        "Beat in the vanilla and eggs, one at a time.",
        "Gradually beat in the dry ingredients until just combined.",
        "Stir in the chocolate chips.",
        "Drop by rounded tablespoons onto ungreased baking sheets and bake for 9 to 11 minutes."
      ]
    }

## Streaming

You can stream structured outputs, which allows you to start processing the
response as it's being generated, without having to wait for the entire output
to be complete. This can improve the perceived performance of your application.

The streamed chunks will be valid partial JSON strings, which can be
concatenated to form the final, complete JSON object.

### Python

    from google import genai
    from pydantic import BaseModel, Field
    from typing import Literal

    class Feedback(BaseModel):
        sentiment: Literal["positive", "neutral", "negative"]
        summary: str

    client = genai.Client()
    prompt = "The new UI is incredibly intuitive and visually appealing. Great job. Add a very long summary to test streaming!"

    response_stream = client.models.generate_content_stream(
        model="gemini-3-flash-preview",
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_json_schema": Feedback.model_json_schema(),
        },
    )

    for chunk in response_stream:
        print(chunk.candidates[0].content.parts[0].text)

### JavaScript

    import { GoogleGenAI } from "@google/genai";
    import { z } from "zod";
    import { zodToJsonSchema } from "zod-to-json-schema";

    const ai = new GoogleGenAI({});
    const prompt = "The new UI is incredibly intuitive and visually appealing. Great job! Add a very long summary to test streaming!";

    const feedbackSchema = z.object({
      sentiment: z.enum(["positive", "neutral", "negative"]),
      summary: z.string(),
    });

    const stream = await ai.models.generateContentStream({
      model: "gemini-3-flash-preview",
      contents: prompt,
      config: {
        responseMimeType: "application/json",
        responseJsonSchema: zodToJsonSchema(feedbackSchema),
      },
    });

    for await (const chunk of stream) {
      console.log(chunk.candidates[0].content.parts[0].text)
    }

## Structured outputs with tools

> [!WARNING]
> **Preview:** This feature is available only to Gemini 3 series models, `gemini-3.1-pro-preview` and `gemini-3-flash-preview`.

Gemini 3 lets you combine Structured Outputs with built-in tools, including
[Grounding with Google Search](https://ai.google.dev/gemini-api/docs/google-search),
[URL Context](https://ai.google.dev/gemini-api/docs/url-context),
[Code Execution](https://ai.google.dev/gemini-api/docs/code-execution),
[File Search](https://ai.google.dev/gemini-api/docs/file-search#structured-output), and
[Function Calling](https://ai.google.dev/gemini-api/docs/function-calling).

### Python

    from google import genai
    from pydantic import BaseModel, Field
    from typing import List

    class MatchResult(BaseModel):
        winner: str = Field(description="The name of the winner.")
        final_match_score: str = Field(description="The final match score.")
        scorers: List[str] = Field(description="The name of the scorer.")

    client = genai.Client()

    response = client.models.generate_content(
        model="gemini-3.1-pro-preview",
        contents="Search for all details for the latest Euro.",
        config={
            "tools": [
                {"google_search": {}},
                {"url_context": {}}
            ],
            "response_mime_type": "application/json",
            "response_json_schema": MatchResult.model_json_schema(),
        },  
    )

    result = MatchResult.model_validate_json(response.text)
    print(result)

### JavaScript

    import { GoogleGenAI } from "@google/genai";
    import { z } from "zod";
    import { zodToJsonSchema } from "zod-to-json-schema";

    const ai = new GoogleGenAI({});

    const matchSchema = z.object({
      winner: z.string().describe("The name of the winner."),
      final_match_score: z.string().describe("The final score."),
      scorers: z.array(z.string()).describe("The name of the scorer.")
    });

    async function run() {
      const response = await ai.models.generateContent({
        model: "gemini-3.1-pro-preview",
        contents: "Search for all details for the latest Euro.",
        config: {
          tools: [
            { googleSearch: {} },
            { urlContext: {} }
          ],
          responseMimeType: "application/json",
          responseJsonSchema: zodToJsonSchema(matchSchema),
        },
      });

      const match = matchSchema.parse(JSON.parse(response.text));
      console.log(match);
    }

    run();

### REST

    curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro-preview:generateContent" \
      -H "x-goog-api-key: $GEMINI_API_KEY" \
      -H 'Content-Type: application/json' \
      -X POST \
      -d '{
        "contents": [{
          "parts": [{"text": "Search for all details for the latest Euro."}]
        }],
        "tools": [
          {"googleSearch": {}},
          {"urlContext": {}}
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": {
                "type": "object",
                "properties": {
                    "winner": {"type": "string", "description": "The name of the winner."},
                    "final_match_score": {"type": "string", "description": "The final score."},
                    "scorers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "The name of the scorer."
                    }
                },
                "required": ["winner", "final_match_score", "scorers"]
            }
        }
      }'

## JSON schema support

To generate a JSON object, set the `response_mime_type` in the generation configuration to `application/json` and provide a `response_json_schema`. The schema must be a valid [JSON Schema](https://json-schema.org/) that describes the desired output format.

The model will then generate a response that is a syntactically valid JSON string matching the provided schema. When using structured outputs, the model will produce outputs in the same order as the keys in the schema.

Gemini's structured output mode supports a subset of the [JSON Schema](https://json-schema.org) specification.

The following values of `type` are supported:

- **`string`**: For text.
- **`number`**: For floating-point numbers.
- **`integer`**: For whole numbers.
- **`boolean`**: For true/false values.
- **`object`**: For structured data with key-value pairs.
- **`array`**: For lists of items.
- **`null`** : To allow a property to be null, include `"null"` in the type array (e.g., `{"type": ["string", "null"]}`).

These descriptive properties help guide the model:

- **`title`**: A short description of a property.
- **`description`**: A longer and more detailed description of a property.

### Type-specific properties

**For `object` values:**

- **`properties`**: An object where each key is a property name and each value is a schema for that property.
- **`required`**: An array of strings, listing which properties are mandatory.
- **`additionalProperties`** : Controls whether properties not listed in `properties` are allowed. Can be a boolean or a schema.

**For `string` values:**

- **`enum`**: Lists a specific set of possible strings for classification tasks.
- **`format`** : Specifies a syntax for the string, such as `date-time`, `date`, `time`.

**For `number` and `integer` values:**

- **`enum`**: Lists a specific set of possible numeric values.
- **`minimum`**: The minimum inclusive value.
- **`maximum`**: The maximum inclusive value.

**For `array` values:**

- **`items`**: Defines the schema for all items in the array.
- **`prefixItems`**: Defines a list of schemas for the first N items, allowing for tuple-like structures.
- **`minItems`**: The minimum number of items in the array.
- **`maxItems`**: The maximum number of items in the array.

## Model support

The following models support structured output:

| Model | Structured Outputs |
|---|---|
| Gemini 3.1 Pro Preview | ✔️ |
| Gemini 3 Flash Preview | ✔️ |
| Gemini 2.5 Pro | ✔️ |
| Gemini 2.5 Flash | ✔️ |
| Gemini 2.5 Flash-Lite | ✔️ |
| Gemini 2.0 Flash | ✔️\* |
| Gemini 2.0 Flash-Lite | ✔️\* |

*\* Note that Gemini 2.0 requires an explicit `propertyOrdering` list within the JSON input to define the preferred structure. You can find an example in this [cookbook](https://github.com/google-gemini/cookbook/blob/main/examples/Pdf_structured_outputs_on_invoices_and_forms.ipynb).*

## Structured outputs vs. function calling

Both structured outputs and function calling use JSON schemas, but they serve different purposes:

| Feature | Primary Use Case |
|---|---|
| **Structured Outputs** | **Formatting the final response to the user.** Use this when you want the model's *answer* to be in a specific format (e.g., extracting data from a document to save to a database). |
| **Function Calling** | **Taking action during the conversation.** Use this when the model needs to *ask you* to perform a task (e.g., "get current weather") before it can provide a final answer. |

## Best practices

- **Clear descriptions:** Use the `description` field in your schema to provide clear instructions to the model about what each property represents. This is crucial for guiding the model's output.
- **Strong typing:** Use specific types (`integer`, `string`, `enum`) whenever possible. If a parameter has a limited set of valid values, use an `enum`.
- **Prompt engineering:** Clearly state in your prompt what you want the model to do. For example, "Extract the following information from the text..." or "Classify this feedback according to the provided schema...".
- **Validation:** While structured output guarantees syntactically correct JSON, it does not guarantee the values are semantically correct. Always validate the final output in your application code before using it.
- **Error handling:** Implement robust error handling in your application to gracefully manage cases where the model's output, while schema-compliant, may not meet your business logic requirements.

## Limitations

- **Schema subset:** Not all features of the JSON Schema specification are supported. The model ignores unsupported properties.
- **Schema complexity:** The API may reject very large or deeply nested schemas. If you encounter errors, try simplifying your schema by shortening property names, reducing nesting, or limiting the number of constraints.


Function calling lets you connect models to external tools and APIs.
Instead of generating text responses, the model determines when to call specific
functions and provides the necessary parameters to execute real-world actions.
This allows the model to act as a bridge between natural language and real-world
actions and data. Function calling has 3 primary use cases:

- **Augment Knowledge:** Access information from external sources like databases, APIs, and knowledge bases.
- **Extend Capabilities:** Use external tools to perform computations and extend the limitations of the model, such as using a calculator or creating charts.
- **Take Actions:** Interact with external systems using APIs, such as scheduling appointments, creating invoices, sending emails, or controlling smart home devices.

<button value="weather">Get Weather</button> <button value="meeting" default="">Schedule Meeting</button> <button value="chart">Create Chart</button>

### Python

    from google import genai
    from google.genai import types

    # Define the function declaration for the model
    schedule_meeting_function = {
        "name": "schedule_meeting",
        "description": "Schedules a meeting with specified attendees at a given time and date.",
        "parameters": {
            "type": "object",
            "properties": {
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of people attending the meeting.",
                },
                "date": {
                    "type": "string",
                    "description": "Date of the meeting (e.g., '2024-07-29')",
                },
                "time": {
                    "type": "string",
                    "description": "Time of the meeting (e.g., '15:00')",
                },
                "topic": {
                    "type": "string",
                    "description": "The subject or topic of the meeting.",
                },
            },
            "required": ["attendees", "date", "time", "topic"],
        },
    }

    # Configure the client and tools
    client = genai.Client()
    tools = types.Tool(function_declarations=[schedule_meeting_function])
    config = types.GenerateContentConfig(tools=[tools])

    # Send request with function declarations
    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents="Schedule a meeting with Bob and Alice for 03/14/2025 at 10:00 AM about the Q3 planning.",
        config=config,
    )

    # Check for a function call
    if response.candidates[0].content.parts[0].function_call:
        function_call = response.candidates[0].content.parts[0].function_call
        print(f"Function to call: {function_call.name}")
        print(f"Arguments: {function_call.args}")
        #  In a real app, you would call your function here:
        #  result = schedule_meeting(**function_call.args)
    else:
        print("No function call found in the response.")
        print(response.text)

### JavaScript

    import { GoogleGenAI, Type } from '@google/genai';

    // Configure the client
    const ai = new GoogleGenAI({});

    // Define the function declaration for the model
    const scheduleMeetingFunctionDeclaration = {
      name: 'schedule_meeting',
      description: 'Schedules a meeting with specified attendees at a given time and date.',
      parameters: {
        type: Type.OBJECT,
        properties: {
          attendees: {
            type: Type.ARRAY,
            items: { type: Type.STRING },
            description: 'List of people attending the meeting.',
          },
          date: {
            type: Type.STRING,
            description: 'Date of the meeting (e.g., "2024-07-29")',
          },
          time: {
            type: Type.STRING,
            description: 'Time of the meeting (e.g., "15:00")',
          },
          topic: {
            type: Type.STRING,
            description: 'The subject or topic of the meeting.',
          },
        },
        required: ['attendees', 'date', 'time', 'topic'],
      },
    };

    // Send request with function declarations
    const response = await ai.models.generateContent({
      model: 'gemini-3-flash-preview',
      contents: 'Schedule a meeting with Bob and Alice for 03/27/2025 at 10:00 AM about the Q3 planning.',
      config: {
        tools: [{
          functionDeclarations: [scheduleMeetingFunctionDeclaration]
        }],
      },
    });

    // Check for function calls in the response
    if (response.functionCalls && response.functionCalls.length > 0) {
      const functionCall = response.functionCalls[0]; // Assuming one function call
      console.log(`Function to call: ${functionCall.name}`);
      console.log(`Arguments: ${JSON.stringify(functionCall.args)}`);
      // In a real app, you would call your actual function here:
      // const result = await scheduleMeeting(functionCall.args);
    } else {
      console.log("No function call found in the response.");
      console.log(response.text);
    }

### REST

    curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent" \
      -H "x-goog-api-key: $GEMINI_API_KEY" \
      -H 'Content-Type: application/json' \
      -X POST \
      -d '{
        "contents": [
          {
            "role": "user",
            "parts": [
              {
                "text": "Schedule a meeting with Bob and Alice for 03/27/2025 at 10:00 AM about the Q3 planning."
              }
            ]
          }
        ],
        "tools": [
          {
            "functionDeclarations": [
              {
                "name": "schedule_meeting",
                "description": "Schedules a meeting with specified attendees at a given time and date.",
                "parameters": {
                  "type": "object",
                  "properties": {
                    "attendees": {
                      "type": "array",
                      "items": {"type": "string"},
                      "description": "List of people attending the meeting."
                    },
                    "date": {
                      "type": "string",
                      "description": "Date of the meeting (e.g., '2024-07-29')"
                    },
                    "time": {
                      "type": "string",
                      "description": "Time of the meeting (e.g., '15:00')"
                    },
                    "topic": {
                      "type": "string",
                      "description": "The subject or topic of the meeting."
                    }
                  },
                  "required": ["attendees", "date", "time", "topic"]
                }
              }
            ]
          }
        ]
      }'

## How function calling works

![function calling
overview](https://ai.google.dev/static/gemini-api/docs/images/function-calling-overview.png)

Function calling involves a structured interaction between your application, the
model, and external functions. Here's a breakdown of the process:

1. **Define Function Declaration:** Define the function declaration in your application code. Function Declarations describe the function's name, parameters, and purpose to the model.
2. **Call LLM with function declarations:** Send user prompt along with the function declaration(s) to the model. It analyzes the request and determines if a function call would be helpful. If so, it responds with a structured JSON object.
3. **Execute Function Code (Your Responsibility):** The Model *doesn't* execute the function itself. It's your application's responsibility to process the response and check for Function Call, if
   - **Yes**: Extract the name and args of the function and execute the corresponding function in your application.
   - **No:** The model has provided a direct text response to the prompt (this flow is less emphasized in the example but is a possible outcome).
4. **Create User friendly response:** If a function was executed, capture the result and send it back to the model in a subsequent turn of the conversation. It will use the result to generate a final, user-friendly response that incorporates the information from the function call.

This process can be repeated over multiple turns, allowing for complex
interactions and workflows. The model also supports calling multiple functions
in a single turn ([parallel function
calling](https://ai.google.dev/gemini-api/docs/function-calling#parallel_function_calling)) and in
sequence ([compositional function
calling](https://ai.google.dev/gemini-api/docs/function-calling#compositional_function_calling)).

### Step 1: Define a function declaration

Define a function and its declaration within your application code that allows
users to set light values and make an API request. This function could call
external services or APIs.

### Python

    # Define a function that the model can call to control smart lights
    set_light_values_declaration = {
        "name": "set_light_values",
        "description": "Sets the brightness and color temperature of a light.",
        "parameters": {
            "type": "object",
            "properties": {
                "brightness": {
                    "type": "integer",
                    "description": "Light level from 0 to 100. Zero is off and 100 is full brightness",
                },
                "color_temp": {
                    "type": "string",
                    "enum": ["daylight", "cool", "warm"],
                    "description": "Color temperature of the light fixture, which can be `daylight`, `cool` or `warm`.",
                },
            },
            "required": ["brightness", "color_temp"],
        },
    }

    # This is the actual function that would be called based on the model's suggestion
    def set_light_values(brightness: int, color_temp: str) -> dict[str, int | str]:
        """Set the brightness and color temperature of a room light. (mock API).

        Args:
            brightness: Light level from 0 to 100. Zero is off and 100 is full brightness
            color_temp: Color temperature of the light fixture, which can be `daylight`, `cool` or `warm`.

        Returns:
            A dictionary containing the set brightness and color temperature.
        """
        return {"brightness": brightness, "colorTemperature": color_temp}

### JavaScript

    import { Type } from '@google/genai';

    // Define a function that the model can call to control smart lights
    const setLightValuesFunctionDeclaration = {
      name: 'set_light_values',
      description: 'Sets the brightness and color temperature of a light.',
      parameters: {
        type: Type.OBJECT,
        properties: {
          brightness: {
            type: Type.NUMBER,
            description: 'Light level from 0 to 100. Zero is off and 100 is full brightness',
          },
          color_temp: {
            type: Type.STRING,
            enum: ['daylight', 'cool', 'warm'],
            description: 'Color temperature of the light fixture, which can be `daylight`, `cool` or `warm`.',
          },
        },
        required: ['brightness', 'color_temp'],
      },
    };

    /**

    *   Set the brightness and color temperature of a room light. (mock API)
    *   @param {number} brightness - Light level from 0 to 100. Zero is off and 100 is full brightness
    *   @param {string} color_temp - Color temperature of the light fixture, which can be `daylight`, `cool` or `warm`.
    *   @return {Object} A dictionary containing the set brightness and color temperature.
    */
    function setLightValues(brightness, color_temp) {
      return {
        brightness: brightness,
        colorTemperature: color_temp
      };
    }

### Step 2: Call the model with function declarations

Once you have defined your function declarations, you can prompt the model to
use them. It analyzes the prompt and function declarations and decides whether
to respond directly or to call a function. If a function is called, the response
object will contain a function call suggestion.

### Python

    from google.genai import types

    # Configure the client and tools
    client = genai.Client()
    tools = types.Tool(function_declarations=[set_light_values_declaration])
    config = types.GenerateContentConfig(tools=[tools])

    # Define user prompt
    contents = [
        types.Content(
            role="user", parts=[types.Part(text="Turn the lights down to a romantic level")]
        )
    ]

    # Send request with function declarations
    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=contents,
        config=config,
    )

    print(response.candidates[0].content.parts[0].function_call)

### JavaScript

    import { GoogleGenAI } from '@google/genai';

    // Generation config with function declaration
    const config = {
      tools: [{
        functionDeclarations: [setLightValuesFunctionDeclaration]
      }]
    };

    // Configure the client
    const ai = new GoogleGenAI({});

    // Define user prompt
    const contents = [
      {
        role: 'user',
        parts: [{ text: 'Turn the lights down to a romantic level' }]
      }
    ];

    // Send request with function declarations
    const response = await ai.models.generateContent({
      model: 'gemini-3-flash-preview',
      contents: contents,
      config: config
    });

    console.log(response.functionCalls[0]);

The model then returns a `functionCall` object in an OpenAPI compatible
schema specifying how to call one or more of the declared functions in order to
respond to the user's question.

### Python

    id=None args={'color_temp': 'warm', 'brightness': 25} name='set_light_values'

### JavaScript

    {
      name: 'set_light_values',
      args: { brightness: 25, color_temp: 'warm' }
    }

### Step 3: Execute set_light_values function code

Extract the function call details from the model's response, parse the arguments
, and execute the `set_light_values` function.

### Python

    # Extract tool call details, it may not be in the first part.
    tool_call = response.candidates[0].content.parts[0].function_call

    if tool_call.name == "set_light_values":
        result = set_light_values(**tool_call.args)
        print(f"Function execution result: {result}")

### JavaScript

    // Extract tool call details
    const tool_call = response.functionCalls[0]

    let result;
    if (tool_call.name === 'set_light_values') {
      result = setLightValues(tool_call.args.brightness, tool_call.args.color_temp);
      console.log(`Function execution result: ${JSON.stringify(result)}`);
    }

### Step 4: Create user friendly response with function result and call the model again

Finally, send the result of the function execution back to the model so it can
incorporate this information into its final response to the user.

### Python

    from google import genai
    from google.genai import types

    # Create a function response part
    function_response_part = types.Part.from_function_response(
        name=tool_call.name,
        response={"result": result},
    )

    # Append function call and result of the function execution to contents
    contents.append(response.candidates[0].content) # Append the content from the model's response.
    contents.append(types.Content(role="user", parts=[function_response_part])) # Append the function response

    client = genai.Client()
    final_response = client.models.generate_content(
        model="gemini-3-flash-preview",
        config=config,
        contents=contents,
    )

    print(final_response.text)

### JavaScript

    // Create a function response part
    const function_response_part = {
      name: tool_call.name,
      response: { result }
    }

    // Append function call and result of the function execution to contents
    contents.push(response.candidates[0].content);
    contents.push({ role: 'user', parts: [{ functionResponse: function_response_part }] });

    // Get the final response from the model
    const final_response = await ai.models.generateContent({
      model: 'gemini-3-flash-preview',
      contents: contents,
      config: config
    });

    console.log(final_response.text);

This completes the function calling flow. The model successfully used the
`set_light_values` function to perform the request action of the user.

## Function declarations

When you implement function calling in a prompt, you create a `tools` object,
which contains one or more `function declarations`. You define functions using
JSON, specifically with a [select subset](https://ai.google.dev/api/caching#Schema)
of the [OpenAPI schema](https://spec.openapis.org/oas/v3.0.3#schemaw) format. A
single function declaration can include the following parameters:

- `name` (string): A unique name for the function (`get_weather_forecast`, `send_email`). Use descriptive names without spaces or special characters (use underscores or camelCase).
- `description` (string): A clear and detailed explanation of the function's purpose and capabilities. This is crucial for the model to understand when to use the function. Be specific and provide examples if helpful ("Finds theaters based on location and optionally movie title which is currently playing in theaters.").
- `parameters` (object): Defines the input parameters the function expects.
  - `type` (string): Specifies the overall data type, such as `object`.
  - `properties` (object): Lists individual parameters, each with:
    - `type` (string): The data type of the parameter, such as `string`, `integer`, `boolean, array`.
    - `description` (string): A description of the parameter's purpose and format. Provide examples and constraints ("The city and state, e.g., 'San Francisco, CA' or a zip code e.g., '95616'.").
    - `enum` (array, optional): If the parameter values are from a fixed set, use "enum" to list the allowed values instead of just describing them in the description. This improves accuracy ("enum": \["daylight", "cool", "warm"\]).
  - `required` (array): An array of strings listing the parameter names that are mandatory for the function to operate.

You can also construct `FunctionDeclarations` from Python functions directly using
`types.FunctionDeclaration.from_callable(client=client, callable=your_function)`.

## Function calling with thinking models

Gemini 3 and 2.5 series models use an internal ["thinking"](https://ai.google.dev/gemini-api/docs/thinking) process to reason through requests. This
significantly improves function calling performance,
allowing the model to better determine when to call a function and which
parameters to use. Because the Gemini API is stateless, models use
[thought signatures](https://ai.google.dev/gemini-api/docs/thought-signatures) to maintain context
across multi-turn conversations.

This section covers advanced management of thought signatures and is only
necessary if you're manually constructing API requests (e.g., via REST) or
manipulating conversation history.

**If you're using the [Google GenAI SDKs](https://ai.google.dev/gemini-api/docs/libraries) (our
official libraries), you don't need to manage this process** . The SDKs
automatically handle the necessary steps, as shown in the earlier
[example](https://ai.google.dev/gemini-api/docs/function-calling#step-4).

### Managing conversation history manually

If you modify the conversation history manually, instead of sending the
[complete previous response](https://ai.google.dev/gemini-api/docs/function-calling#step-4) you
must correctly handle the `thought_signature` included in the model's turn.

Follow these rules to ensure the model's context is preserved:

- Always send the `thought_signature` back to the model inside its original [`Part`](https://ai.google.dev/api#request-body-structure).
- Don't merge a `Part` containing a signature with one that does not. This breaks the positional context of the thought.
- Don't combine two `Parts` that both contain signatures, as the signature strings cannot be merged.

#### Gemini 3 thought signatures

In Gemini 3, any [`Part`](https://ai.google.dev/api#request-body-structure) of a model response
may contain a thought signature.
While we generally recommend returning signatures from all `Part` types,
passing back thought signatures is mandatory for function calling. Unless you
are manipulating conversation history manually, the Google GenAI SDK will
handle thought signatures automatically.

If you are manipulating conversation history manually, refer to the
[Thoughts Signatures](https://ai.google.dev/gemini-api/docs/thought-signatures) page for complete
guidance and details on handling thought signatures for Gemini 3.

### Inspecting thought signatures

While not necessary for implementation, you can inspect the response to see the
`thought_signature` for debugging or educational purposes.

### Python

    import base64
    # After receiving a response from a model with thinking enabled
    # response = client.models.generate_content(...)

    # The signature is attached to the response part containing the function call
    part = response.candidates[0].content.parts[0]
    if part.thought_signature:
      print(base64.b64encode(part.thought_signature).decode("utf-8"))

### JavaScript

    // After receiving a response from a model with thinking enabled
    // const response = await ai.models.generateContent(...)

    // The signature is attached to the response part containing the function call
    const part = response.candidates[0].content.parts[0];
    if (part.thoughtSignature) {
      console.log(part.thoughtSignature);
    }

Learn more about limitations and usage of thought signatures, and about thinking
models in general, on the [Thinking](https://ai.google.dev/gemini-api/docs/thinking#signatures) page.

## Parallel function calling

In addition to single turn function calling, you can also call multiple
functions at once. Parallel function calling lets you execute multiple functions
at once and is used when the functions are not dependent on each other. This is
useful in scenarios like gathering data from multiple independent sources, such
as retrieving customer details from different databases or checking inventory
levels across various warehouses or performing multiple actions such as
converting your apartment into a disco.

When the model initiates multiple function calls in a single turn, you don't need to return the `function_result` objects in the same order that the `function_call` objects were received. The Gemini API maps each result back to its corresponding call using the `tool_use_id` (which matches the `call_id` from the model's output). This lets you execute your functions asynchronously and append the results to your list as they complete.

### Python

    power_disco_ball = {
        "name": "power_disco_ball",
        "description": "Powers the spinning disco ball.",
        "parameters": {
            "type": "object",
            "properties": {
                "power": {
                    "type": "boolean",
                    "description": "Whether to turn the disco ball on or off.",
                }
            },
            "required": ["power"],
        },
    }

    start_music = {
        "name": "start_music",
        "description": "Play some music matching the specified parameters.",
        "parameters": {
            "type": "object",
            "properties": {
                "energetic": {
                    "type": "boolean",
                    "description": "Whether the music is energetic or not.",
                },
                "loud": {
                    "type": "boolean",
                    "description": "Whether the music is loud or not.",
                },
            },
            "required": ["energetic", "loud"],
        },
    }

    dim_lights = {
        "name": "dim_lights",
        "description": "Dim the lights.",
        "parameters": {
            "type": "object",
            "properties": {
                "brightness": {
                    "type": "number",
                    "description": "The brightness of the lights, 0.0 is off, 1.0 is full.",
                }
            },
            "required": ["brightness"],
        },
    }

### JavaScript

    import { Type } from '@google/genai';

    const powerDiscoBall = {
      name: 'power_disco_ball',
      description: 'Powers the spinning disco ball.',
      parameters: {
        type: Type.OBJECT,
        properties: {
          power: {
            type: Type.BOOLEAN,
            description: 'Whether to turn the disco ball on or off.'
          }
        },
        required: ['power']
      }
    };

    const startMusic = {
      name: 'start_music',
      description: 'Play some music matching the specified parameters.',
      parameters: {
        type: Type.OBJECT,
        properties: {
          energetic: {
            type: Type.BOOLEAN,
            description: 'Whether the music is energetic or not.'
          },
          loud: {
            type: Type.BOOLEAN,
            description: 'Whether the music is loud or not.'
          }
        },
        required: ['energetic', 'loud']
      }
    };

    const dimLights = {
      name: 'dim_lights',
      description: 'Dim the lights.',
      parameters: {
        type: Type.OBJECT,
        properties: {
          brightness: {
            type: Type.NUMBER,
            description: 'The brightness of the lights, 0.0 is off, 1.0 is full.'
          }
        },
        required: ['brightness']
      }
    };

Configure the function calling mode to allow using all of the specified tools.
To learn more, you can read about
[configuring function calling](https://ai.google.dev/gemini-api/docs/function-calling#function_calling_modes).

### Python

    from google import genai
    from google.genai import types

    # Configure the client and tools
    client = genai.Client()
    house_tools = [
        types.Tool(function_declarations=[power_disco_ball, start_music, dim_lights])
    ]
    config = types.GenerateContentConfig(
        tools=house_tools,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(
            disable=True
        ),
        # Force the model to call 'any' function, instead of chatting.
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode='ANY')
        ),
    )

    chat = client.chats.create(model="gemini-3-flash-preview", config=config)
    response = chat.send_message("Turn this place into a party!")

    # Print out each of the function calls requested from this single call
    print("Example 1: Forced function calling")
    for fn in response.function_calls:
        args = ", ".join(f"{key}={val}" for key, val in fn.args.items())
        print(f"{fn.name}({args})")

### JavaScript

    import { GoogleGenAI } from '@google/genai';

    // Set up function declarations
    const houseFns = [powerDiscoBall, startMusic, dimLights];

    const config = {
        tools: [{
            functionDeclarations: houseFns
        }],
        // Force the model to call 'any' function, instead of chatting.
        toolConfig: {
            functionCallingConfig: {
                mode: 'any'
            }
        }
    };

    // Configure the client
    const ai = new GoogleGenAI({});

    // Create a chat session
    const chat = ai.chats.create({
        model: 'gemini-3-flash-preview',
        config: config
    });
    const response = await chat.sendMessage({message: 'Turn this place into a party!'});

    // Print out each of the function calls requested from this single call
    console.log("Example 1: Forced function calling");
    for (const fn of response.functionCalls) {
        const args = Object.entries(fn.args)
            .map(([key, val]) => `${key}=${val}`)
            .join(', ');
        console.log(`${fn.name}(${args})`);
    }

Each of the printed results reflects a single function call that the model has
requested. To send the results back, include the responses in the same order as
they were requested.

The Python SDK supports [automatic function calling](https://ai.google.dev/gemini-api/docs/function-calling#automatic_function_calling_python_only),
which automatically converts Python functions to declarations, handles the
function call execution and response cycle for you. Following is an example for
the disco use case.

> [!NOTE]
> **Note:** Automatic Function Calling is a Python SDK only feature at the moment.

### Python

    from google import genai
    from google.genai import types

    # Actual function implementations
    def power_disco_ball_impl(power: bool) -> dict:
        """Powers the spinning disco ball.

        Args:
            power: Whether to turn the disco ball on or off.

        Returns:
            A status dictionary indicating the current state.
        """
        return {"status": f"Disco ball powered {'on' if power else 'off'}"}

    def start_music_impl(energetic: bool, loud: bool) -> dict:
        """Play some music matching the specified parameters.

        Args:
            energetic: Whether the music is energetic or not.
            loud: Whether the music is loud or not.

        Returns:
            A dictionary containing the music settings.
        """
        music_type = "energetic" if energetic else "chill"
        volume = "loud" if loud else "quiet"
        return {"music_type": music_type, "volume": volume}

    def dim_lights_impl(brightness: float) -> dict:
        """Dim the lights.

        Args:
            brightness: The brightness of the lights, 0.0 is off, 1.0 is full.

        Returns:
            A dictionary containing the new brightness setting.
        """
        return {"brightness": brightness}

    # Configure the client
    client = genai.Client()
    config = types.GenerateContentConfig(
        tools=[power_disco_ball_impl, start_music_impl, dim_lights_impl]
    )

    # Make the request
    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents="Do everything you need to this place into party!",
        config=config,
    )

    print("\nExample 2: Automatic function calling")
    print(response.text)
    # I've turned on the disco ball, started playing loud and energetic music, and dimmed the lights to 50% brightness. Let's get this party started!

## Compositional function calling

Compositional or sequential function calling allows Gemini to chain multiple
function calls together to fulfill a complex request. For example, to answer
"Get the temperature in my current location", the Gemini API might first invoke
a `get_current_location()` function followed by a `get_weather()` function that
takes the location as a parameter.

The following example demonstrates how to implement compositional function
calling using the Python SDK and automatic function calling.

### Python

This example uses the automatic function calling feature of the
`google-genai` Python SDK. The SDK automatically converts the Python
functions to the required schema, executes the function calls when requested
by the model, and sends the results back to the model to complete the task.

    import os
    from google import genai
    from google.genai import types

    # Example Functions
    def get_weather_forecast(location: str) -> dict:
        """Gets the current weather temperature for a given location."""
        print(f"Tool Call: get_weather_forecast(location={location})")
        # TODO: Make API call
        print("Tool Response: {'temperature': 25, 'unit': 'celsius'}")
        return {"temperature": 25, "unit": "celsius"}  # Dummy response

    def set_thermostat_temperature(temperature: int) -> dict:
        """Sets the thermostat to a desired temperature."""
        print(f"Tool Call: set_thermostat_temperature(temperature={temperature})")
        # TODO: Interact with a thermostat API
        print("Tool Response: {'status': 'success'}")
        return {"status": "success"}

    # Configure the client and model
    client = genai.Client()
    config = types.GenerateContentConfig(
        tools=[get_weather_forecast, set_thermostat_temperature]
    )

    # Make the request
    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents="If it's warmer than 20°C in London, set the thermostat to 20°C, otherwise set it to 18°C.",
        config=config,
    )

    # Print the final, user-facing response
    print(response.text)

**Expected Output**

When you run the code, you will see the SDK orchestrating the function
calls. The model first calls `get_weather_forecast`, receives the
temperature, and then calls `set_thermostat_temperature` with the correct
value based on the logic in the prompt.

    Tool Call: get_weather_forecast(location=London)
    Tool Response: {'temperature': 25, 'unit': 'celsius'}
    Tool Call: set_thermostat_temperature(temperature=20)
    Tool Response: {'status': 'success'}
    OK. I've set the thermostat to 20°C.

### JavaScript

This example shows how to use JavaScript/TypeScript SDK to do comopositional
function calling using a manual execution loop.

    import { GoogleGenAI, Type } from "@google/genai";

    // Configure the client
    const ai = new GoogleGenAI({});

    // Example Functions
    function get_weather_forecast({ location }) {
      console.log(`Tool Call: get_weather_forecast(location=${location})`);
      // TODO: Make API call
      console.log("Tool Response: {'temperature': 25, 'unit': 'celsius'}");
      return { temperature: 25, unit: "celsius" };
    }

    function set_thermostat_temperature({ temperature }) {
      console.log(
        `Tool Call: set_thermostat_temperature(temperature=${temperature})`,
      );
      // TODO: Make API call
      console.log("Tool Response: {'status': 'success'}");
      return { status: "success" };
    }

    const toolFunctions = {
      get_weather_forecast,
      set_thermostat_temperature,
    };

    const tools = [
      {
        functionDeclarations: [
          {
            name: "get_weather_forecast",
            description:
              "Gets the current weather temperature for a given location.",
            parameters: {
              type: Type.OBJECT,
              properties: {
                location: {
                  type: Type.STRING,
                },
              },
              required: ["location"],
            },
          },
          {
            name: "set_thermostat_temperature",
            description: "Sets the thermostat to a desired temperature.",
            parameters: {
              type: Type.OBJECT,
              properties: {
                temperature: {
                  type: Type.NUMBER,
                },
              },
              required: ["temperature"],
            },
          },
        ],
      },
    ];

    // Prompt for the model
    let contents = [
      {
        role: "user",
        parts: [
          {
            text: "If it's warmer than 20°C in London, set the thermostat to 20°C, otherwise set it to 18°C.",
          },
        ],
      },
    ];

    // Loop until the model has no more function calls to make
    while (true) {
      const result = await ai.models.generateContent({
        model: "gemini-3-flash-preview",
        contents,
        config: { tools },
      });

      if (result.functionCalls && result.functionCalls.length > 0) {
        const functionCall = result.functionCalls[0];

        const { name, args } = functionCall;

        if (!toolFunctions[name]) {
          throw new Error(`Unknown function call: ${name}`);
        }

        // Call the function and get the response.
        const toolResponse = toolFunctions[name](args);

        const functionResponsePart = {
          name: functionCall.name,
          response: {
            result: toolResponse,
          },
        };

        // Send the function response back to the model.
        contents.push({
          role: "model",
          parts: [
            {
              functionCall: functionCall,
            },
          ],
        });
        contents.push({
          role: "user",
          parts: [
            {
              functionResponse: functionResponsePart,
            },
          ],
        });
      } else {
        // No more function calls, break the loop.
        console.log(result.text);
        break;
      }
    }

**Expected Output**

When you run the code, you will see the SDK orchestrating the function
calls. The model first calls `get_weather_forecast`, receives the
temperature, and then calls `set_thermostat_temperature` with the correct
value based on the logic in the prompt.

    Tool Call: get_weather_forecast(location=London)
    Tool Response: {'temperature': 25, 'unit': 'celsius'}
    Tool Call: set_thermostat_temperature(temperature=20)
    Tool Response: {'status': 'success'}
    OK. It's 25°C in London, so I've set the thermostat to 20°C.

Compositional function calling is a native [Live
API](https://ai.google.dev/gemini-api/docs/live) feature. This means Live API
can handle the function calling similar to the Python SDK.

### Python

    # Light control schemas
    turn_on_the_lights_schema = {'name': 'turn_on_the_lights'}
    turn_off_the_lights_schema = {'name': 'turn_off_the_lights'}

    prompt = """
      Hey, can you write run some python code to turn on the lights, wait 10s and then turn off the lights?
      """

    tools = [
        {'code_execution': {}},
        {'function_declarations': [turn_on_the_lights_schema, turn_off_the_lights_schema]}
    ]

    await run(prompt, tools=tools, modality="AUDIO")

### JavaScript

    // Light control schemas
    const turnOnTheLightsSchema = { name: 'turn_on_the_lights' };
    const turnOffTheLightsSchema = { name: 'turn_off_the_lights' };

    const prompt = `
      Hey, can you write run some python code to turn on the lights, wait 10s and then turn off the lights?
    `;

    const tools = [
      { codeExecution: {} },
      { functionDeclarations: [turnOnTheLightsSchema, turnOffTheLightsSchema] }
    ];

    await run(prompt, tools=tools, modality="AUDIO")

## Function calling modes

The Gemini API lets you control how the model uses the provided tools
(function declarations). Specifically, you can set the mode within
the.`function_calling_config`.

- `AUTO (Default)`: The model decides whether to generate a natural language response or suggest a function call based on the prompt and context. This is the most flexible mode and recommended for most scenarios.
- `ANY`: The model is constrained to always predict a function call and guarantees function schema adherence. If `allowed_function_names` is not specified, the model can choose from any of the provided function declarations. If `allowed_function_names` is provided as a list, the model can only choose from the functions in that list. Use this mode when you require a function call response to every prompt (if applicable).
- `NONE`: The model is *prohibited* from making function calls. This is equivalent to sending a request without any function declarations. Use this to temporarily disable function calling without removing your tool definitions.
- `VALIDATED` (Preview): The model is constrained to predict either function
  calls or natural language, and ensures function schema adherence. If
  `allowed_function_names` is not provided, the model picks from all of the
  available function declarations. If `allowed_function_names` is provided, the
  model picks from the set of allowed functions.

### Python

    from google.genai import types

    # Configure function calling mode
    tool_config = types.ToolConfig(
        function_calling_config=types.FunctionCallingConfig(
            mode="ANY", allowed_function_names=["get_current_temperature"]
        )
    )

    # Create the generation config
    config = types.GenerateContentConfig(
        tools=[tools],  # not defined here.
        tool_config=tool_config,
    )

### JavaScript

    import { FunctionCallingConfigMode } from '@google/genai';

    // Configure function calling mode
    const toolConfig = {
      functionCallingConfig: {
        mode: FunctionCallingConfigMode.ANY,
        allowedFunctionNames: ['get_current_temperature']
      }
    };

    // Create the generation config
    const config = {
      tools: tools, // not defined here.
      toolConfig: toolConfig,
    };

## Automatic function calling (Python only)

When using the Python SDK, you can provide Python functions directly as tools.
The SDK converts these functions into declarations, manages the function call
execution, and handles the response cycle for you. Define your function with
type hints and a docstring. For optimal results, it is recommended to use
[Google-style docstrings.](https://google.github.io/styleguide/pyguide.html#383-functions-and-methods)
The SDK will then automatically:

1. Detect function call responses from the model.
2. Call the corresponding Python function in your code.
3. Send the function's response back to the model.
4. Return the model's final text response.

The SDK currently doesn't parse argument descriptions into the property
description slots of the generated function declaration. Instead, it sends the
entire docstring as the top-level function description.

### Python

    from google import genai
    from google.genai import types

    # Define the function with type hints and docstring
    def get_current_temperature(location: str) -> dict:
        """Gets the current temperature for a given location.

        Args:
            location: The city and state, e.g. San Francisco, CA

        Returns:
            A dictionary containing the temperature and unit.
        """
        # ... (implementation) ...
        return {"temperature": 25, "unit": "Celsius"}

    # Configure the client
    client = genai.Client()
    config = types.GenerateContentConfig(
        tools=[get_current_temperature]
    )  # Pass the function itself

    # Make the request
    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents="What's the temperature in Boston?",
        config=config,
    )

    print(response.text)  # The SDK handles the function call and returns the final text

You can disable automatic function calling with:

### Python

    config = types.GenerateContentConfig(
        tools=[get_current_temperature],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
    )

### Automatic function schema declaration

The API is able to describe any of the following types. `Pydantic` types are
allowed, as long as the fields defined on them are also composed of allowed
types. Dict types (like `dict[str: int]`) are not well supported here, don't
use them.

### Python

    AllowedType = (
      int | float | bool | str | list['AllowedType'] | pydantic.BaseModel)

To see what the inferred schema looks like, you can convert it using
[`from_callable`](https://googleapis.github.io/python-genai/genai.html#genai.types.FunctionDeclaration.from_callable):

### Python

    from google import genai
    from google.genai import types

    def multiply(a: float, b: float):
        """Returns a * b."""
        return a * b

    client = genai.Client()
    fn_decl = types.FunctionDeclaration.from_callable(callable=multiply, client=client)

    # to_json_dict() provides a clean JSON representation.
    print(fn_decl.to_json_dict())

## Multi-tool use: Combine native tools with function calling

You can enable multiple tools combining native tools with
function calling at the same time. Here's an example that enables two tools,
[Grounding with Google Search](https://ai.google.dev/gemini-api/docs/grounding) and
[code execution](https://ai.google.dev/gemini-api/docs/code-execution), in a request using the
[Live API](https://ai.google.dev/gemini-api/docs/live).

> [!NOTE]
> **Note:** Multi-tool use is a-[Live API](https://ai.google.dev/gemini-api/docs/live) only feature at the moment. The `run()` function declaration, which handles the asynchronous websocket setup, is omitted for brevity.

### Python

    # Multiple tasks example - combining lights, code execution, and search
    prompt = """
      Hey, I need you to do three things for me.

        1.  Turn on the lights.
        2.  Then compute the largest prime palindrome under 100000.
        3.  Then use Google Search to look up information about the largest earthquake in California the week of Dec 5 2024.

      Thanks!
      """

    tools = [
        {'google_search': {}},
        {'code_execution': {}},
        {'function_declarations': [turn_on_the_lights_schema, turn_off_the_lights_schema]} # not defined here.
    ]

    # Execute the prompt with specified tools in audio modality
    await run(prompt, tools=tools, modality="AUDIO")

### JavaScript

    // Multiple tasks example - combining lights, code execution, and search
    const prompt = `
      Hey, I need you to do three things for me.

        1.  Turn on the lights.
        2.  Then compute the largest prime palindrome under 100000.
        3.  Then use Google Search to look up information about the largest earthquake in California the week of Dec 5 2024.

      Thanks!
    `;

    const tools = [
      { googleSearch: {} },
      { codeExecution: {} },
      { functionDeclarations: [turnOnTheLightsSchema, turnOffTheLightsSchema] } // not defined here.
    ];

    // Execute the prompt with specified tools in audio modality
    await run(prompt, {tools: tools, modality: "AUDIO"});

Python developers can try this out in the [Live API Tool Use
notebook](https://colab.research.google.com/github/google-gemini/cookbook/blob/main/quickstarts/Get_started_LiveAPI_tools.ipynb).

## Multimodal function responses

> [!NOTE]
> **Note:** This feature is available for [Gemini 3](https://ai.google.dev/gemini-api/docs/gemini-3) series models.

For Gemini 3 series models, you can include multimodal content in
the function response parts that you send to the model. The model can process
this multimodal content in its next turn to produce a more informed response.
The following MIME types are supported for multimodal content in function
responses:

- **Images** : `image/png`, `image/jpeg`, `image/webp`
- **Documents** : `application/pdf`, `text/plain`

To include multimodal data in a function response, include it as one or more
parts nested within the `functionResponse` part. Each multimodal part must
contain `inlineData`. If you reference a multimodal part from
within the structured `response` field, it must contain a unique `displayName`.

You can also reference a multimodal part from within the structured `response`
field of the `functionResponse` part by using the JSON reference format
`{"$ref": "<displayName>"}`. The model substitutes the reference with the
multimodal content when processing the response. Each `displayName` can only be
referenced once in the structured `response` field.

The following example shows a message containing a `functionResponse` for a
function named `get_image` and a nested part containing image data with
`displayName: "instrument.jpg"`. The `functionResponse`'s `response` field
references this image part:

### Python

    from google import genai
    from google.genai import types

    import requests

    client = genai.Client()

    # This is a manual, two turn multimodal function calling workflow:

    # 1. Define the function tool
    get_image_declaration = types.FunctionDeclaration(
      name="get_image",
      description="Retrieves the image file reference for a specific order item.",
      parameters={
          "type": "object",
          "properties": {
              "item_name": {
                  "type": "string",
                  "description": "The name or description of the item ordered (e.g., 'instrument')."
              }
          },
          "required": ["item_name"],
      },
    )
    tool_config = types.Tool(function_declarations=[get_image_declaration])

    # 2. Send a message that triggers the tool
    prompt = "Show me the instrument I ordered last month."
    response_1 = client.models.generate_content(
      model="gemini-3-flash-preview",
      contents=[prompt],
      config=types.GenerateContentConfig(
          tools=[tool_config],
      )
    )

    # 3. Handle the function call
    function_call = response_1.function_calls[0]
    requested_item = function_call.args["item_name"]
    print(f"Model wants to call: {function_call.name}")

    # Execute your tool (e.g., call an API)
    # (This is a mock response for the example)
    print(f"Calling external tool for: {requested_item}")

    function_response_data = {
      "image_ref": {"$ref": "instrument.jpg"},
    }
    image_path = "https://goo.gle/instrument-img"
    image_bytes = requests.get(image_path).content
    function_response_multimodal_data = types.FunctionResponsePart(
      inline_data=types.FunctionResponseBlob(
        mime_type="image/jpeg",
        display_name="instrument.jpg",
        data=image_bytes,
      )
    )

    # 4. Send the tool's result back
    # Append this turn's messages to history for a final response.
    history = [
      types.Content(role="user", parts=[types.Part(text=prompt)]),
      response_1.candidates[0].content,
      types.Content(
        role="user",
        parts=[
            types.Part.from_function_response(
              name=function_call.name,
              response=function_response_data,
              parts=[function_response_multimodal_data]
            )
        ],
      )
    ]

    response_2 = client.models.generate_content(
      model="gemini-3-flash-preview",
      contents=history,
      config=types.GenerateContentConfig(
          tools=[tool_config],
          thinking_config=types.ThinkingConfig(include_thoughts=True)
      ),
    )

    print(f"\nFinal model response: {response_2.text}")

### JavaScript

    import { GoogleGenAI, Type } from '@google/genai';

    const client = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });

    // This is a manual, two turn multimodal function calling workflow:
    // 1. Define the function tool
    const getImageDeclaration = {
      name: 'get_image',
      description: 'Retrieves the image file reference for a specific order item.',
      parameters: {
        type: Type.OBJECT,
        properties: {
          item_name: {
            type: Type.STRING,
            description: "The name or description of the item ordered (e.g., 'instrument').",
          },
        },
        required: ['item_name'],
      },
    };

    const toolConfig = {
      functionDeclarations: [getImageDeclaration],
    };

    // 2. Send a message that triggers the tool
    const prompt = 'Show me the instrument I ordered last month.';
    const response1 = await client.models.generateContent({
      model: 'gemini-3-flash-preview',
      contents: prompt,
      config: {
        tools: [toolConfig],
      },
    });

    // 3. Handle the function call
    const functionCall = response1.functionCalls[0];
    const requestedItem = functionCall.args.item_name;
    console.log(`Model wants to call: ${functionCall.name}`);

    // Execute your tool (e.g., call an API)
    // (This is a mock response for the example)
    console.log(`Calling external tool for: ${requestedItem}`);

    const functionResponseData = {
      image_ref: { $ref: 'instrument.jpg' },
    };

    const imageUrl = "https://goo.gle/instrument-img";
    const response = await fetch(imageUrl);
    const imageArrayBuffer = await response.arrayBuffer();
    const base64ImageData = Buffer.from(imageArrayBuffer).toString('base64');

    const functionResponseMultimodalData = {
      inlineData: {
        mimeType: 'image/jpeg',
        displayName: 'instrument.jpg',
        data: base64ImageData,
      },
    };

    // 4. Send the tool's result back
    // Append this turn's messages to history for a final response.
    const history = [
      { role: 'user', parts: [{ text: prompt }] },
      response1.candidates[0].content,
      {
        role: 'tool',
        parts: [
          {
            functionResponse: {
              name: functionCall.name,
              response: functionResponseData,
              parts: [functionResponseMultimodalData],
            },
          },
        ],
      },
    ];

    const response2 = await client.models.generateContent({
      model: 'gemini-3-flash-preview',
      contents: history,
      config: {
        tools: [toolConfig],
        thinkingConfig: { includeThoughts: true },
      },
    });

    console.log(`\nFinal model response: ${response2.text}`);

### REST

    IMG_URL="https://goo.gle/instrument-img"

    MIME_TYPE=$(curl -sIL "$IMG_URL" | grep -i '^content-type:' | awk -F ': ' '{print $2}' | sed 's/\r$//' | head -n 1)
    if [[ -z "$MIME_TYPE" || ! "$MIME_TYPE" == image/* ]]; then
      MIME_TYPE="image/jpeg"
    fi

    # Check for macOS
    if [[ "$(uname)" == "Darwin" ]]; then
      IMAGE_B64=$(curl -sL "$IMG_URL" | base64 -b 0)
    elif [[ "$(base64 --version 2>&1)" = *"FreeBSD"* ]]; then
      IMAGE_B64=$(curl -sL "$IMG_URL" | base64)
    else
      IMAGE_B64=$(curl -sL "$IMG_URL" | base64 -w0)
    fi

    curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent" \
      -H "x-goog-api-key: $GEMINI_API_KEY" \
      -H 'Content-Type: application/json' \
      -X POST \
      -d '{
        "contents": [
          ...,
          {
            "role": "user",
            "parts": [
            {
                "functionResponse": {
                  "name": "get_image",
                  "response": {
                    "image_ref": {
                      "$ref": "instrument.jpg"
                    }
                  },
                  "parts": [
                    {
                      "inlineData": {
                        "displayName": "instrument.jpg",
                        "mimeType":"'"$MIME_TYPE"'",
                        "data": "'"$IMAGE_B64"'"
                      }
                    }
                  ]
                }
              }
            ]
          }
        ]
      }'

## Function calling with Structured output

> [!NOTE]
> **Note:** This feature is available for [Gemini 3](https://ai.google.dev/gemini-api/docs/gemini-3) series models.

For Gemini 3 series models, you can use function calling with [structured output](https://ai.google.dev/gemini-api/docs/structured-output). This lets the model predict function calls or outputs that adhere to a specific schema. As a result, you receive consistently formatted responses when the model doesn't generate function calls.

## Model context protocol (MCP)

[Model Context Protocol (MCP)](https://modelcontextprotocol.io/introduction) is
an open standard for connecting AI applications with external tools and data.
MCP provides a common protocol for models to access context, such as functions
(tools), data sources (resources), or predefined prompts.

The Gemini SDKs have built-in support for the MCP, reducing boilerplate code and
offering
[automatic tool calling](https://ai.google.dev/gemini-api/docs/function-calling#automatic_function_calling_python_only)
for MCP tools. When the model generates an MCP tool call, the Python and
JavaScript client SDK can automatically execute the MCP tool and send the
response back to the model in a subsequent request, continuing this loop until
no more tool calls are made by the model.

Here, you can find an example of how to use a local MCP server with Gemini and
`mcp` SDK.

### Python

Make sure the latest version of the
[`mcp` SDK](https://modelcontextprotocol.io/introduction) is installed on
your platform of choice.

    pip install mcp

> [!NOTE]
> **Note:** Python supports automatic tool calling by passing in the `ClientSession` into the `tools` parameters. If you want to disable it, you can provide `automatic_function_calling` with disabled `True`.

    import os
    import asyncio
    from datetime import datetime
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from google import genai

    client = genai.Client()

    # Create server parameters for stdio connection
    server_params = StdioServerParameters(
        command="npx",  # Executable
        args=["-y", "@philschmid/weather-mcp"],  # MCP Server
        env=None,  # Optional environment variables
    )

    async def run():
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                # Prompt to get the weather for the current day in London.
                prompt = f"What is the weather in London in {datetime.now().strftime('%Y-%m-%d')}?"

                # Initialize the connection between client and server
                await session.initialize()

                # Send request to the model with MCP function declarations
                response = await client.aio.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=genai.types.GenerateContentConfig(
                        temperature=0,
                        tools=[session],  # uses the session, will automatically call the tool
                        # Uncomment if you **don't** want the SDK to automatically call the tool
                        # automatic_function_calling=genai.types.AutomaticFunctionCallingConfig(
                        #     disable=True
                        # ),
                    ),
                )
                print(response.text)

    # Start the asyncio event loop and run the main function
    asyncio.run(run())

### JavaScript

Make sure the latest version of the `mcp` SDK is installed on your platform
of choice.

    npm install @modelcontextprotocol/sdk

> [!NOTE]
> **Note:** JavaScript supports automatic tool calling by wrapping the `client` with `mcpToTool`. If you want to disable it, you can provide `automaticFunctionCalling` with disabled `true`.

    import { GoogleGenAI, FunctionCallingConfigMode , mcpToTool} from '@google/genai';
    import { Client } from "@modelcontextprotocol/sdk/client/index.js";
    import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

    // Create server parameters for stdio connection
    const serverParams = new StdioClientTransport({
      command: "npx", // Executable
      args: ["-y", "@philschmid/weather-mcp"] // MCP Server
    });

    const client = new Client(
      {
        name: "example-client",
        version: "1.0.0"
      }
    );

    // Configure the client
    const ai = new GoogleGenAI({});

    // Initialize the connection between client and server
    await client.connect(serverParams);

    // Send request to the model with MCP tools
    const response = await ai.models.generateContent({
      model: "gemini-2.5-flash",
      contents: `What is the weather in London in ${new Date().toLocaleDateString()}?`,
      config: {
        tools: [mcpToTool(client)],  // uses the session, will automatically call the tool
        // Uncomment if you **don't** want the sdk to automatically call the tool
        // automaticFunctionCalling: {
        //   disable: true,
        // },
      },
    });
    console.log(response.text)

    // Close the connection
    await client.close();

### Limitations with built-in MCP support

Built-in MCP support is a [experimental](https://ai.google.dev/gemini-api/docs/models#preview)
feature in our SDKs and has the following limitations:

- Only tools are supported, not resources nor prompts
- It is available for the Python and JavaScript/TypeScript SDK.
- Breaking changes might occur in future releases.

Manual integration of MCP servers is always an option if these limit what you're
building.

## Supported models

This section lists models and their function calling capabilities. Experimental
models are not included. You can find a comprehensive capabilities overview on
the [model overview](https://ai.google.dev/gemini-api/docs/models) page.

| Model | Function Calling | Parallel Function Calling | Compositional Function Calling |
|---|---|---|---|
| Gemini 3.1 Pro Preview | ✔️ | ✔️ | ✔️ |
| Gemini 3 Flash Preview | ✔️ | ✔️ | ✔️ |
| Gemini 2.5 Pro | ✔️ | ✔️ | ✔️ |
| Gemini 2.5 Flash | ✔️ | ✔️ | ✔️ |
| Gemini 2.5 Flash-Lite | ✔️ | ✔️ | ✔️ |
| Gemini 2.0 Flash | ✔️ | ✔️ | ✔️ |
| Gemini 2.0 Flash-Lite | X | X | X |

## Best practices

- **Function and Parameter Descriptions:** Be extremely clear and specific in your descriptions. The model relies on these to choose the correct function and provide appropriate arguments.
- **Naming:** Use descriptive function names (without spaces, periods, or dashes).
- **Strong Typing:** Use specific types (integer, string, enum) for parameters to reduce errors. If a parameter has a limited set of valid values, use an enum.
- **Tool Selection:** While the model can use an arbitrary number of tools, providing too many can increase the risk of selecting an incorrect or suboptimal tool. For best results, aim to provide only the relevant tools for the context or task, ideally keeping the active set to a maximum of 10-20. Consider dynamic tool selection based on conversation context if you have a large total number of tools.
- **Prompt Engineering:**
  - Provide context: Tell the model its role (e.g., "You are a helpful weather assistant.").
  - Give instructions: Specify how and when to use functions (e.g., "Don't guess dates; always use a future date for forecasts.").
  - Encourage clarification: Instruct the model to ask clarifying questions if needed.
  - See [Agentic workflows](https://ai.google.dev/gemini-api/docs/prompting-strategies#agentic-workflows) for further strategies on designing these prompts. Here is an example of a tested [system instruction](https://ai.google.dev/gemini-api/docs/prompting-strategies#agentic-si-template).
- **Temperature:** Use a low temperature (e.g., 0) for more deterministic and
  reliable function calls.

  > [!NOTE]
  > When using Gemini 3 models, we strongly recommend keeping the `temperature` at its default value of 1.0. Changing the temperature (setting it below 1.0) may lead to unexpected behavior, such as looping or degraded performance, particularly in complex mathematical or reasoning tasks.

- **Validation:** If a function call has significant consequences (e.g.,
  placing an order), validate the call with the user before executing it.

- **Check Finish Reason:** Always check the [`finishReason`](https://ai.google.dev/api/generate-content#FinishReason)
  in the model's response to handle cases where the model failed to generate a
  valid function call.

- **Error Handling**: Implement robust error handling in your functions to
  gracefully handle unexpected inputs or API failures. Return informative
  error messages that the model can use to generate helpful responses to the
  user.

- **Security:** Be mindful of security when calling external APIs. Use
  appropriate authentication and authorization mechanisms. Avoid exposing
  sensitive data in function calls.

- **Token Limits:** Function descriptions and parameters count towards your
  input token limit. If you're hitting token limits, consider limiting the
  number of functions or the length of the descriptions, break down complex
  tasks into smaller, more focused function sets.

- **Mix of bash and custom tools** For those building with a mix of bash and custom tools, Gemini 3.1 Pro Preview
  comes with a separate endpoint available via the API called
  [`gemini-3.1-pro-preview-customtools`](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-pro-preview#gemini-31-pro-preview-customtools).

## Notes and limitations

- Only a [subset of the OpenAPI
  schema](https://ai.google.dev/api/caching#FunctionDeclaration) is supported.
- For `ANY` mode, the API may reject very large or deeply nested schemas. If you encounter errors, try simplifying your function parameter and response schemas by shortening property names, reducing nesting, or limiting the number of function declarations.
- Supported parameter types in Python are limited.
- Automatic function calling is a Python SDK feature only.


---
sidebar_position: 2
title: "Development"
---

## Writing A Custom Toolkit

Toolkits are defined in a single Python file, with a top level docstring with metadata and a `Tools` class.

:::warning Use Async Functions for Future Compatibility
Tool methods should generally be defined as `async` to ensure compatibility with future Open WebUI versions. The backend is progressively moving toward fully async execution, and synchronous functions may block execution or cause issues in future releases.
:::

### Example Top-Level Docstring

```python
"""
title: String Inverse
author: Your Name
author_url: https://website.com
git_url: https://github.com/username/string-reverse.git
description: This tool calculates the inverse of a string
required_open_webui_version: 0.4.0
requirements: langchain-openai, langgraph, ollama, langchain_ollama
version: 0.4.0
licence: MIT
"""
```

### Tools Class

Tools have to be defined as methods within a class called `Tools`, with optional subclasses called `Valves` and `UserValves`, for example:

```python
class Tools:
    def __init__(self):
        """Initialize the Tool."""
        self.valves = self.Valves()

    class Valves(BaseModel):
        api_key: str = Field("", description="Your API key here")

    def reverse_string(self, string: str) -> str:
        """
        Reverses the input string.
        :param string: The string to reverse
        """
        # example usage of valves
        if self.valves.api_key != "42":
            return "Wrong API key"
        return string[::-1]
```

### Type Hints
Each tool must have type hints for arguments. The types may also be nested, such as `queries_and_docs: list[tuple[str, int]]`. Those type hints are used to generate the JSON schema that is sent to the model. Tools without type hints will work with a lot less consistency.

### Valves and UserValves - (optional, but HIGHLY encouraged)

Valves and UserValves are used for specifying customizable settings of the Tool, you can read more on the dedicated [Valves & UserValves page](/features/extensibility/plugin/development/valves).

### Optional Arguments
Below is a list of optional arguments your tools can depend on:
- `__event_emitter__`: Emit events (see following section)
- `__event_call__`: Same as event emitter but can be used for user interactions. The server-side timeout for event calls is configurable via [`WEBSOCKET_EVENT_CALLER_TIMEOUT`](/reference/env-configuration#websocket_event_caller_timeout) (default: 300s).
- `__user__`: A dictionary with user information. It also contains the `UserValves` object in `__user__["valves"]`.
- `__metadata__`: Dictionary with chat metadata
- `__messages__`: List of previous messages
- `__files__`: Attached files
- `__model__`: A dictionary with model information
- `__oauth_token__`: A dictionary containing the user's valid, automatically refreshed OAuth token payload. This is the **new, recommended, and secure** way to access user tokens for making authenticated API calls. The dictionary typically contains `access_token`, `id_token`, and other provider-specific data.

For more information about `__oauth_token__` and how to configure this token to be sent to tools, check out the OAuth section in the [environment variable docs page](https://docs.openwebui.com/reference/env-configuration/) and the [SSO documentation](https://docs.openwebui.com/features/auth/).

Just add them as argument to any method of your Tool class just like `__user__` in the example above.

#### Using the OAuth Token in a Tool

When building tools that need to interact with external APIs on the user's behalf, you can now directly access their OAuth token. This removes the need for fragile cookie scraping and ensures the token is always valid.

**Example:** A tool that calls an external API using the user's access token.

```python
import httpx
from typing import Optional

class Tools:
    # ... other class setup ...

    async def get_user_profile_from_external_api(self, __oauth_token__: Optional[dict] = None) -> str:
        """
        Fetches user profile data from a secure external API using their OAuth access token.

        :param __oauth_token__: Injected by Open WebUI, contains the user's token data.
        """
        if not __oauth_token__ or "access_token" not in __oauth_token__:
            return "Error: User is not authenticated via OAuth or token is unavailable."

        access_token = __oauth_token__["access_token"]

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get("https://api.my-service.com/v1/profile", headers=headers)
                response.raise_for_status() # Raise an exception for bad status codes
                return f"API Response: {response.json()}"
        except httpx.HTTPStatusError as e:
            return f"Error: Failed to fetch data from API. Status: {e.response.status_code}"
        except Exception as e:
            return f"An unexpected error occurred: {e}"
```

### Event Emitters

Event Emitters are used to add additional information to the chat interface. Similarly to Filter Outlets, Event Emitters are capable of appending content to the chat. Unlike Filter Outlets, they are not capable of stripping information. Additionally, emitters can be activated at any stage during the Tool.

**⚠️ CRITICAL: Function Calling Mode Compatibility**

Event Emitter behavior is **significantly different** depending on your function calling mode. The function calling mode is controlled by the `function_calling` parameter:

- **Default Mode**: Uses traditional function calling approach with wider model compatibility
- **Native Mode (Agentic Mode)**: Leverages model's built-in tool-calling capabilities for reduced latency and autonomous behavior

Before using event emitters, you must understand these critical limitations:

- **Default Mode** (`function_calling = "default"`): Full event emitter support with all event types working as expected
- **Native Mode (Agentic Mode)** (`function_calling = "native"`): **Limited event emitter support** - many event types don't work properly due to native function calling bypassing Open WebUI's custom tool processing pipeline

**When to Use Each Mode:**
For a comprehensive guide on choosing a function calling mode, including model requirements and administrator setup, refer to the [**Central Tool Calling Guide**](/features/extensibility/plugin/tools#tool-calling-modes-default-vs-native).

In general:
- **Use Default Mode** when you need full event emitter functionality, complex tool interactions, or real-time UI updates.
- **Use Native Mode (Agentic Mode)** when you have a quality model and need reduced latency, autonomous tool selection, and system-level tools (Agentic Research, Knowledge Base exploration, Memory) without complex custom emitter requirements.

#### Function Calling Mode Configuration

You can configure the function calling mode in two places:

1. **Administrator Level**: Go to **Admin Panel > Settings > Models > Model Specific Settings > Advanced Parameters > Function Calling** (set to "Default" or "Native").
2. **Per-request basis**: Set `params.function_calling = "native"` or `"default"` in Chat Controls > Advanced Params.

If the model seems to be unable to call the tool, make sure it is enabled (either via the Model page or via the `+` sign next to the chat input field).

:::info Native Mode & Built-in Tools
When writing custom tools, be aware that Open WebUI also provides **built-in system tools** when Native Mode is enabled. For details on built-in tools, function calling modes, and model requirements, see the [**Tool Calling Modes Guide**](/features/extensibility/plugin/tools#tool-calling-modes-default-vs-native).
:::


#### Complete Event Type Compatibility Matrix

Here's the comprehensive breakdown of how each event type behaves across function calling modes:

| Event Type | Default Mode Functionality | Native Mode Functionality | Status |
|------------|---------------------------|--------------------------|--------|
| `status` | ✅ Full support - Updates status history during tool execution | ✅ **Identical** - Tracks function execution status | **COMPATIBLE** |
| `message` | ✅ Full support - Appends incremental content during streaming | ❌ **BROKEN** - Gets overwritten by native completion snapshots | **INCOMPATIBLE** |
| `chat:completion` | ✅ Full support - Handles streaming responses and completion data | ⚠️ **LIMITED** - Carries function results but may overwrite tool updates | **PARTIALLY COMPATIBLE** |
| `chat:message:delta` | ✅ Full support - Streams delta content during execution | ❌ **BROKEN** - Content gets replaced by native function snapshots | **INCOMPATIBLE** |
| `chat:message` | ✅ Full support - Replaces entire message content cleanly | ❌ **BROKEN** - Gets overwritten by subsequent native completions | **INCOMPATIBLE** |
| `replace` | ✅ Full support - Replaces content with precise control | ❌ **BROKEN** - Replaced content gets overwritten immediately | **INCOMPATIBLE** |
| `chat:message:files` / `files` | ✅ Full support - Handles file attachments in messages | ✅ **Identical** - Processes files from function outputs | **COMPATIBLE** |
| `chat:message:error` | ✅ Full support - Displays error notifications | ✅ **Identical** - Shows function call errors | **COMPATIBLE** |
| `chat:message:follow_ups` | ✅ Full support - Shows follow-up suggestions | ✅ **Identical** - Displays function-generated follow-ups | **COMPATIBLE** |
| `chat:title` | ✅ Full support - Updates chat title dynamically | ✅ **Identical** - Updates title based on function interactions | **COMPATIBLE** |
| `chat:tags` | ✅ Full support - Modifies chat tags | ✅ **Identical** - Manages tags from function outputs | **COMPATIBLE** |
| `chat:tasks:cancel` | ✅ Full support - Cancels ongoing tasks | ✅ **Identical** - Cancels native function executions | **COMPATIBLE** |
| `citation` / `source` | ✅ Full support - Handles citations with full metadata | ✅ **Identical** - Processes function-generated citations | **COMPATIBLE** |
| `notification` | ✅ Full support - Shows toast notifications | ✅ **Identical** - Displays function execution notifications | **COMPATIBLE** |
| `confirmation` | ✅ Full support - Requests user confirmations | ✅ **Identical** - Confirms function executions | **COMPATIBLE** |
| `execute` | ✅ Full support - Executes code dynamically | ✅ **Identical** - Runs function-generated code | **COMPATIBLE** |
| `input` | ✅ Full support - Requests user input with full UI | ✅ **Identical** - Collects input for functions | **COMPATIBLE** |

#### Why Native Mode Breaks Certain Event Types

In **Native Mode**, the server constructs content blocks from streaming model output and repeatedly emits `"chat:completion"` events with full serialized content snapshots. The client treats these snapshots as authoritative and completely replaces message content, effectively overwriting any prior tool-emitted updates like `message`, `chat:message`, or `replace` events.

**Technical Details:**
- `middleware.py` adds tools directly to form data for native model handling
- Streaming handler emits repeated content snapshots via `chat:completion` events
- Client's `chatCompletionEventHandler` treats snapshots as complete replacements: `message.content = content`
- This causes tool-emitted content updates to flicker and disappear

#### Best Practices and Recommendations

**For Tools Requiring Real-time UI Updates:**
```python
class Tools:
    def __init__(self):
        # Add a note about function calling mode requirements
        self.description = "This tool requires Default function calling mode for full functionality"

    async def interactive_tool(self, prompt: str, __event_emitter__=None) -> str:
        """
        ⚠️ This tool requires function_calling = "default" for proper event emission
        """
        if not __event_emitter__:
            return "Event emitter not available - ensure Default function calling mode is enabled"

        # Safe to use message events in Default mode
        await __event_emitter__({
            "type": "message",
            "data": {"content": "Processing step 1..."}
        })
        # ... rest of tool logic
```

**For Tools That Must Work in Both Modes:**
```python
async def universal_tool(self, prompt: str, __event_emitter__=None, __metadata__=None) -> str:
    """
    Tool designed to work in both Default and Native function calling modes
    """
    # Check if we're in native mode (this is a rough heuristic)
    is_native_mode = __metadata__ and __metadata__.get("params", {}).get("function_calling") == "native"

    if __event_emitter__:
        if is_native_mode:
            # Use only compatible event types in native mode
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Processing in native mode...", "done": False}
            })
        else:
            # Full event functionality in default mode
            await __event_emitter__({
                "type": "message",
                "data": {"content": "Processing with full event support..."}
            })

    # ... tool logic here

    if __event_emitter__:
        await __event_emitter__({
            "type": "status",
            "data": {"description": "Completed successfully", "done": True}
        })

    return "Tool execution completed"
```

#### Troubleshooting Event Emitter Issues

**Symptoms of Native Mode Conflicts:**
- Tool-emitted messages appear briefly then disappear
- Content flickers during tool execution
- `message` or `replace` events seem to be ignored
- Status updates work but content updates don't persist

**Solutions:**
1. **Switch to Default Mode**: Change `function_calling` from `"native"` to `"default"` in model settings
2. **Use Compatible Event Types**: Stick to `status`, `citation`, `notification`, and other compatible event types in native mode
3. **Implement Mode Detection**: Add logic to detect function calling mode and adjust event usage accordingly
4. **Consider Hybrid Approaches**: Use compatible events for core functionality and degrade gracefully

**Debugging Your Event Emitters:**
```python
async def debug_events_tool(self, __event_emitter__=None, __metadata__=None) -> str:
    """Debug tool to test event emitter functionality"""

    if not __event_emitter__:
        return "No event emitter available"

    # Test various event types
    test_events = [
        {"type": "status", "data": {"description": "Testing status events", "done": False}},
        {"type": "message", "data": {"content": "Testing message events (may not work in native mode)"}},
        {"type": "notification", "data": {"content": "Testing notification events"}},
    ]

    mode_info = "Unknown"
    if __metadata__:
        mode_info = __metadata__.get("params", {}).get("function_calling", "default")

    await __event_emitter__({
        "type": "status",
        "data": {"description": f"Function calling mode: {mode_info}", "done": False}
    })

    for i, event in enumerate(test_events):
        await asyncio.sleep(1)  # Space out events
        await __event_emitter__(event)
        await __event_emitter__({
            "type": "status",
            "data": {"description": f"Sent event {i+1}/{len(test_events)}", "done": False}
        })

    await __event_emitter__({
        "type": "status",
        "data": {"description": "Event testing complete", "done": True}
    })

    return f"Event testing completed in {mode_info} mode. Check for missing or flickering content."
```

There are several specific event types with different behaviors:

#### Status Events ✅ FULLY COMPATIBLE

**Status events work identically in both Default and Native function calling modes.** This is the most reliable event type for providing real-time feedback during tool execution.

Status events add live status updates to a message while it's performing steps. These can be emitted at any stage during tool execution. Status messages appear right above the message content and are essential for tools that delay the LLM response or process large amounts of information.

**Basic Status Event Structure:**
```python
await __event_emitter__({
    "type": "status",
    "data": {
        "description": "Message that shows up in the chat",
        "done": False,        # False = still processing, True = completed
        "hidden": False       # False = visible, True = auto-hide when done
    }
})
```

**Status Event Parameters:**
- `description`: The status message text shown to users
- `done`: Boolean indicating if this status represents completion
- `hidden`: Boolean to auto-hide the status once `done: True` is set

<details>
<summary>Basic Status Example</summary>

```python
async def data_processing_tool(
        self, data_file: str, __user__: dict, __event_emitter__=None
    ) -> str:
        """
        Processes a large data file with status updates
        ✅ Works in both Default and Native function calling modes
        """

        if not __event_emitter__:
            return "Processing completed (no status updates available)"

        # Step 1: Loading
        await __event_emitter__({
            "type": "status",
            "data": {"description": "Loading data file...", "done": False}
        })

        # Simulate loading time
        await asyncio.sleep(2)

        # Step 2: Processing
        await __event_emitter__({
            "type": "status",
            "data": {"description": "Analyzing 10,000 records...", "done": False}
        })

        # Simulate processing time
        await asyncio.sleep(3)

        # Step 3: Completion
        await __event_emitter__({
            "type": "status",
            "data": {"description": "Analysis complete!", "done": True, "hidden": False}
        })

        return "Data analysis completed successfully. Found 23 anomalies."
```
</details>

<details>
<summary>Advanced Status with Error Handling</summary>

```python
async def api_integration_tool(
        self, endpoint: str, __event_emitter__=None
    ) -> str:
        """
        Integrates with external API with comprehensive status tracking
        ✅ Compatible with both function calling modes
        """

        if not __event_emitter__:
            return "API integration completed (no status available)"

        try:
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Connecting to API...", "done": False}
            })

            # Simulate API connection
            await asyncio.sleep(1.5)

            await __event_emitter__({
                "type": "status",
                "data": {"description": "Authenticating...", "done": False}
            })

            # Simulate authentication
            await asyncio.sleep(1)

            await __event_emitter__({
                "type": "status",
                "data": {"description": "Fetching data...", "done": False}
            })

            # Simulate data fetching
            await asyncio.sleep(2)

            # Success status
            await __event_emitter__({
                "type": "status",
                "data": {"description": "API integration successful", "done": True}
            })

            return "Successfully retrieved 150 records from the API"

        except Exception as e:
            # Error status - always visible for debugging
            await __event_emitter__({
                "type": "status",
                "data": {"description": f"Error: {str(e)}", "done": True, "hidden": False}
            })

            return f"API integration failed: {str(e)}"
```
</details>

<details>
<summary>Multi-Step Progress Status</summary>

```python
async def batch_processor_tool(
        self, items: list, __event_emitter__=None
    ) -> str:
        """
        Processes items in batches with detailed progress tracking
        ✅ Works perfectly in both function calling modes
        """

        if not __event_emitter__ or not items:
            return "Batch processing completed"

        total_items = len(items)
        batch_size = 10
        completed = 0

        for i in range(0, total_items, batch_size):
            batch = items[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (total_items + batch_size - 1) // batch_size

            # Update status for current batch
            await __event_emitter__({
                "type": "status",
                "data": {
                    "description": f"Processing batch {batch_num}/{total_batches} ({len(batch)} items)...",
                    "done": False
                }
            })

            # Simulate batch processing
            await asyncio.sleep(1)

            completed += len(batch)

            # Progress update
            progress_pct = int((completed / total_items) * 100)
            await __event_emitter__({
                "type": "status",
                "data": {
                    "description": f"Progress: {completed}/{total_items} items ({progress_pct}%)",
                    "done": False
                }
            })

        # Final completion status
        await __event_emitter__({
            "type": "status",
            "data": {
                "description": f"Batch processing complete! Processed {total_items} items",
                "done": True
            }
        })

        return f"Successfully processed {total_items} items in {total_batches} batches"
```
</details>

#### Message Events ⚠️ DEFAULT MODE ONLY

:::warning

**🚨 CRITICAL WARNING: Message events are INCOMPATIBLE with Native function calling mode!**

:::

Message events (`message`, `chat:message`, `chat:message:delta`, `replace`) allow you to append or modify message content at any stage during tool execution. This enables embedding images, rendering web pages, streaming content updates, and creating rich interactive experiences.

**However, these event types have major compatibility issues:**
- ✅ **Default Mode**: Full functionality - content persists and displays properly
- ❌ **Native Mode**: BROKEN - content gets overwritten by completion snapshots and disappears

**Why Message Events Break in Native Mode:**
Native function calling emits repeated `chat:completion` events with full content snapshots that completely replace message content, causing any tool-emitted message updates to flicker and disappear.

**Safe Message Event Structure (Default Mode Only):**
```python
await __event_emitter__({
    "type": "message",  # Also: "chat:message", "chat:message:delta", "replace"
    "data": {"content": "This content will be appended/replaced in the chat"},
    # Note: message types do NOT require a "done" condition
})
```

**Message Event Types:**
- `message` / `chat:message:delta`: Appends content to existing message
- `chat:message` / `replace`: Replaces entire message content
- Both types will be overwritten in Native mode

<details>
<summary>Safe Message Streaming (Default Mode)</summary>

```python
async def streaming_content_tool(
        self, query: str, __event_emitter__=None, __metadata__=None
    ) -> str:
        """
        Streams content updates during processing
        ⚠️ REQUIRES function_calling = "default" - Will not work in Native mode!
        """

        # Check function calling mode (rough detection)
        mode = "unknown"
        if __metadata__:
            mode = __metadata__.get("params", {}).get("function_calling", "default")

        if mode == "native":
            return "❌ This tool requires Default function calling mode. Message streaming is not supported in Native mode due to content overwriting issues."

        if not __event_emitter__:
            return "Event emitter not available"

        # Stream progressive content updates
        content_chunks = [
            "🔍 **Phase 1: Research**\nGathering information about your query...\n\n",
            "📊 **Phase 2: Analysis**\nAnalyzing gathered data patterns...\n\n",
            "✨ **Phase 3: Synthesis**\nGenerating insights and recommendations...\n\n",
            "📝 **Phase 4: Final Report**\nCompiling comprehensive results...\n\n"
        ]

        accumulated_content = ""

        for i, chunk in enumerate(content_chunks):
            accumulated_content += chunk

            # Append this chunk to the message
            await __event_emitter__({
                "type": "message",
                "data": {"content": chunk}
            })

            # Show progress status
            await __event_emitter__({
                "type": "status",
                "data": {
                    "description": f"Processing phase {i+1}/{len(content_chunks)}...",
                    "done": False
                }
            })

            # Simulate processing time
            await asyncio.sleep(2)

        # Final completion
        await __event_emitter__({
            "type": "status",
            "data": {"description": "Content streaming complete!", "done": True}
        })

        return "Content streaming completed successfully. All phases processed."
```
</details>

<details>
<summary>Dynamic Content Replacement (Default Mode)</summary>

```python
async def live_dashboard_tool(
        self, __event_emitter__=None, __metadata__=None
    ) -> str:
        """
        Creates a live-updating dashboard using content replacement
        ⚠️ ONLY WORKS in Default function calling mode
        """

        # Verify we're not in Native mode
        mode = __metadata__.get("params", {}).get("function_calling", "default") if __metadata__ else "default"

        if mode == "native":
            return """
❌ **Native Mode Incompatibility**

This dashboard tool cannot function in Native mode because:
- Content replacement events get overwritten by completion snapshots
- Live updates will flicker and disappear
- Real-time data will not persist in the interface

**Solution:** Switch to Default function calling mode in Model Settings → Advanced Params → Function Calling = "Default"
"""

        if not __event_emitter__:
            return "Dashboard created (static mode - no live updates)"

        # Create initial dashboard
        initial_dashboard = """

# 📊 Live System Dashboard

## System Status: 🟡 Initializing...

### Current Metrics:
- **CPU Usage**: Loading...
- **Memory**: Loading...
- **Active Users**: Loading...
- **Response Time**: Loading...

---
*Last Updated: Initializing...*
"""

        await __event_emitter__({
            "type": "replace",
            "data": {"content": initial_dashboard}
        })

        # Simulate live data updates
        updates = [
            {
                "status": "🟢 Online",
                "cpu": "23%",
                "memory": "64%",
                "users": "1,247",
                "response": "145ms"
            },
            {
                "status": "🟢 Optimal",
                "cpu": "18%",
                "memory": "61%",
                "users": "1,352",
                "response": "132ms"
            },
            {
                "status": "🟡 Busy",
                "cpu": "67%",
                "memory": "78%",
                "users": "1,891",
                "response": "234ms"
            }
        ]

        for i, data in enumerate(updates):
            await asyncio.sleep(3)  # Simulate data collection delay

            updated_dashboard = f"""

# 📊 Live System Dashboard

## System Status: {data['status']}

### Current Metrics:
- **CPU Usage**: {data['cpu']}
- **Memory**: {data['memory']}
- **Active Users**: {data['users']}
- **Response Time**: {data['response']}

---
*Last Updated: {datetime.now().strftime('%H:%M:%S')}*
*Update {i+1}/{len(updates)}*
"""

            # Replace entire dashboard content
            await __event_emitter__({
                "type": "replace",
                "data": {"content": updated_dashboard}
            })

            # Status update
            await __event_emitter__({
                "type": "status",
                "data": {"description": f"Dashboard updated ({i+1}/{len(updates)})", "done": False}
            })

        await __event_emitter__({
            "type": "status",
            "data": {"description": "Live dashboard monitoring complete", "done": True}
        })

        return "Dashboard monitoring session completed."
```
</details>

<details>
<summary>Mode-Safe Message Tool</summary>

```python
async def adaptive_content_tool(
        self, content_type: str, __event_emitter__=None, __metadata__=None
    ) -> str:
        """
        Adapts behavior based on function calling mode
        ✅ Provides best possible experience in both modes
        """

        # Detect function calling mode
        mode = "default"  # Default assumption
        if __metadata__:
            mode = __metadata__.get("params", {}).get("function_calling", "default")

        if not __event_emitter__:
            return f"Generated {content_type} content (no real-time updates available)"

        # Mode-specific behavior
        if mode == "native":
            # Use only compatible events in Native mode
            await __event_emitter__({
                "type": "status",
                "data": {"description": f"Generating {content_type} content in Native mode...", "done": False}
            })

            await asyncio.sleep(2)

            await __event_emitter__({
                "type": "status",
                "data": {"description": "Content generation complete", "done": True}
            })

            # Return content normally - no message events
            return f"""

# {content_type.title()} Content

**Mode**: Native Function Calling (Limited Event Support)

Generated content here... This content is returned as the tool result rather than being streamed via message events.

*Note: Live content updates are not available in Native mode due to event compatibility limitations.*
"""

        else:  # Default mode
            # Full message event functionality available
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Generating content with full streaming support...", "done": False}
            })

            # Stream content progressively
            progressive_content = [
                f"# {content_type.title()} Content\n\n**Mode**: Default Function Calling ✅\n\n",
                "## Section 1: Introduction\nStreaming content in real-time...\n\n",
                "## Section 2: Details\nAdding detailed information...\n\n",
                "## Section 3: Conclusion\nFinalizing content delivery...\n\n",
                "*✅ Content streaming completed successfully!*"
            ]

            for i, chunk in enumerate(progressive_content):
                await __event_emitter__({
                    "type": "message",
                    "data": {"content": chunk}
                })

                await __event_emitter__({
                    "type": "status",
                    "data": {"description": f"Streaming section {i+1}/{len(progressive_content)}...", "done": False}
                })

                await asyncio.sleep(1.5)

            await __event_emitter__({
                "type": "status",
                "data": {"description": "Content streaming complete!", "done": True}
            })

            return "Content has been streamed above with full Default mode capabilities."
```
</details>

#### Citations ✅ FULLY COMPATIBLE

**Citation events work identically in both Default and Native function calling modes.** This event type provides source references and citations in the chat interface, allowing users to click and view source materials.

Citations are essential for tools that retrieve information from external sources, databases, or documents. They provide transparency and allow users to verify information sources.

**Citation Event Structure:**
```python
await __event_emitter__({
    "type": "citation",
    "data": {
        "document": [content],                    # Array of content strings
        "metadata": [                             # Array of metadata objects
            {
                "date_accessed": datetime.now().isoformat(),
                "source": title,
                "author": "Author Name",          # Optional
                "publication_date": "2024-01-01", # Optional
                "url": "https://source-url.com"   # Optional
            }
        ],
        "source": {"name": title, "url": url}    # Primary source info
    }
})
```

**Important Citation Setup:**
When implementing custom citations, you **must** disable automatic citations in your `Tools` class:

```python
def __init__(self):
    self.citation = False  # REQUIRED - prevents automatic citations from overriding custom ones
```

:::warning

**⚠️ Critical Citation Warning:**
If you set `self.citation = True` (or don't set it to `False`), automatic citations will replace any custom citations you send. Always disable automatic citations when using custom citation events.

:::

<details>
<summary>Basic Citation Example</summary>

```python
class Tools:
    def __init__(self):
        self.citation = False  # Disable automatic citations

    async def research_tool(
            self, topic: str, __event_emitter__=None
        ) -> str:
        """
        Researches a topic and provides proper citations
        ✅ Works identically in both Default and Native modes
        """

        if not __event_emitter__:
            return "Research completed (citations not available)"

        # Simulate research findings
        sources = [
            {
                "title": "Advanced AI Systems",
                "url": "https://example.com/ai-systems",
                "content": "Artificial intelligence systems have evolved significantly...",
                "author": "Dr. Jane Smith",
                "date": "2024-03-15"
            },
            {
                "title": "Machine Learning Fundamentals",
                "url": "https://example.com/ml-fundamentals",
                "content": "The core principles of machine learning include...",
                "author": "Prof. John Doe",
                "date": "2024-02-20"
            }
        ]

        # Emit citations for each source
        for source in sources:
            await __event_emitter__({
                "type": "citation",
                "data": {
                    "document": [source["content"]],
                    "metadata": [
                        {
                            "date_accessed": datetime.now().isoformat(),
                            "source": source["title"],
                            "author": source["author"],
                            "publication_date": source["date"],
                            "url": source["url"]
                        }
                    ],
                    "source": {
                        "name": source["title"],
                        "url": source["url"]
                    }
                }
            })

        return f"Research on '{topic}' completed. Found {len(sources)} relevant sources with detailed citations."
```
</details>

<details>
<summary>Advanced Multi-Source Citations</summary>

```python
async def comprehensive_analysis_tool(
        self, query: str, __event_emitter__=None
    ) -> str:
        """
        Performs comprehensive analysis with multiple source types
        ✅ Full compatibility across all function calling modes
        """

        if not __event_emitter__:
            return "Analysis completed"

        # Multiple source types with rich metadata
        research_sources = {
            "academic": [
                {
                    "title": "Neural Network Architecture in Modern AI",
                    "authors": ["Dr. Sarah Chen", "Prof. Michael Rodriguez"],
                    "journal": "Journal of AI Research",
                    "volume": "Vol. 45, Issue 2",
                    "pages": "123-145",
                    "doi": "10.1000/182",
                    "date": "2024-01-15",
                    "content": "This comprehensive study examines the evolution of neural network architectures..."
                }
            ],
            "web_sources": [
                {
                    "title": "Industry AI Implementation Trends",
                    "url": "https://tech-insights.com/ai-trends-2024",
                    "site_name": "TechInsights",
                    "published": "2024-03-01",
                    "content": "Recent industry surveys show that 78% of companies are implementing AI solutions..."
                }
            ],
            "reports": [
                {
                    "title": "Global AI Market Report 2024",
                    "organization": "International Tech Research Institute",
                    "report_number": "ITRI-2024-AI-001",
                    "date": "2024-02-28",
                    "content": "The global artificial intelligence market is projected to reach $1.8 trillion by 2030..."
                }
            ]
        }

        citation_count = 0

        # Process academic sources
        for source in research_sources["academic"]:
            citation_count += 1
            await __event_emitter__({
                "type": "citation",
                "data": {
                    "document": [source["content"]],
                    "metadata": [
                        {
                            "date_accessed": datetime.now().isoformat(),
                            "source": source["title"],
                            "authors": source["authors"],
                            "journal": source["journal"],
                            "volume": source["volume"],
                            "pages": source["pages"],
                            "doi": source["doi"],
                            "publication_date": source["date"],
                            "type": "academic_journal"
                        }
                    ],
                    "source": {
                        "name": f"{source['title']} - {source['journal']}",
                        "url": f"https://doi.org/{source['doi']}"
                    }
                }
            })

        # Process web sources
        for source in research_sources["web_sources"]:
            citation_count += 1
            await __event_emitter__({
                "type": "citation",
                "data": {
                    "document": [source["content"]],
                    "metadata": [
                        {
                            "date_accessed": datetime.now().isoformat(),
                            "source": source["title"],
                            "site_name": source["site_name"],
                            "publication_date": source["published"],
                            "url": source["url"],
                            "type": "web_article"
                        }
                    ],
                    "source": {
                        "name": source["title"],
                        "url": source["url"]
                    }
                }
            })

        # Process reports
        for source in research_sources["reports"]:
            citation_count += 1
            await __event_emitter__({
                "type": "citation",
                "data": {
                    "document": [source["content"]],
                    "metadata": [
                        {
                            "date_accessed": datetime.now().isoformat(),
                            "source": source["title"],
                            "organization": source["organization"],
                            "report_number": source["report_number"],
                            "publication_date": source["date"],
                            "type": "research_report"
                        }
                    ],
                    "source": {
                        "name": f"{source['title']} - {source['organization']}",
                        "url": f"https://reports.example.com/{source['report_number']}"
                    }
                }
            })

        return f"""

# Analysis Complete

Comprehensive analysis of '{query}' has been completed using {citation_count} authoritative sources:

- **{len(research_sources['academic'])}** Academic journal articles
- **{len(research_sources['web_sources'])}** Industry web sources
- **{len(research_sources['reports'])}** Research reports

All sources have been properly cited and are available for review by clicking the citation links above.
"""
```
</details>

<details>
<summary>Database Citation Tool</summary>

```python
async def database_query_tool(
        self, sql_query: str, __event_emitter__=None
    ) -> str:
        """
        Queries database and provides data citations
        ✅ Works perfectly in both function calling modes
        """

        if not __event_emitter__:
            return "Database query executed"

        # Simulate database results with citation metadata
        query_results = [
            {
                "record_id": "USR_001247",
                "data": "John Smith, Software Engineer, joined 2023-01-15",
                "table": "employees",
                "last_updated": "2024-03-10T14:30:00Z",
                "updated_by": "admin_user"
            },
            {
                "record_id": "USR_001248",
                "data": "Jane Wilson, Product Manager, joined 2023-02-20",
                "table": "employees",
                "last_updated": "2024-03-08T09:15:00Z",
                "updated_by": "hr_system"
            }
        ]

        # Create citations for each database record
        for i, record in enumerate(query_results):
            await __event_emitter__({
                "type": "citation",
                "data": {
                    "document": [f"Database Record: {record['data']}"],
                    "metadata": [
                        {
                            "date_accessed": datetime.now().isoformat(),
                            "source": f"Database Table: {record['table']}",
                            "record_id": record['record_id'],
                            "last_updated": record['last_updated'],
                            "updated_by": record['updated_by'],
                            "query": sql_query,
                            "type": "database_record"
                        }
                    ],
                    "source": {
                        "name": f"Record {record['record_id']} - {record['table']}",
                        "url": f"database://internal/tables/{record['table']}/{record['record_id']}"
                    }
                }
            })

        return f"""

# Database Query Results

Executed query: `{sql_query}`

Retrieved **{len(query_results)}** records with complete citation metadata. Each record includes:
- Record ID and source table
- Last modification timestamp
- Update attribution
- Full audit trail

All data sources have been properly cited for transparency and verification.
"""
```
</details>

#### Additional Compatible Event Types ✅

The following event types work identically in both Default and Native function calling modes:

**Notification Events**
```python
await __event_emitter__({
    "type": "notification",
    "data": {"content": "Toast notification message"}
})
```

**File Events**
```python
await __event_emitter__({
    "type": "files", # or "chat:message:files"
    "data": {"files": [{"name": "report.pdf", "url": "/files/report.pdf"}]}
})
```

**Follow-up Events**
```python
await __event_emitter__({
    "type": "chat:message:follow_ups",
    "data": {"follow_ups": ["What about X?", "Tell me more about Y"]}
})
```

**Title Update Events**
```python
await __event_emitter__({
    "type": "chat:title",
    "data": {"title": "New Chat Title"}
})
```

**Tag Events**
```python
await __event_emitter__({
    "type": "chat:tags",
    "data": {"tags": ["research", "analysis", "completed"]}
})
```

**Error Events**
```python
await __event_emitter__({
    "type": "chat:message:error",
    "data": {"content": "Error message to display"}
})
```

**Confirmation Events**
```python
await __event_emitter__({
    "type": "confirmation",
    "data": {"message": "Are you sure you want to continue?"}
})
```

**Input Request Events**
```python
await __event_emitter__({
    "type": "input",
    "data": {"prompt": "Please enter additional information:"}
})
```

**Code Execution Events**
```python
await __event_emitter__({
    "type": "execute",
    "data": {"code": "print('Hello from tool-generated code!')"}
})
```

#### Comprehensive Function Calling Mode Guide

Choosing the right function calling mode is crucial for your tool's functionality. This guide helps you make an informed decision based on your specific requirements.

**Mode Comparison Overview:**

| Aspect | Default Mode | Native Mode |
|--------|-------------|-------------|
| **Latency** | Higher - processes through Open WebUI pipeline | Lower - direct model handling |
| **Event Support** | ✅ Full - all event types work perfectly | ⚠️ Limited - many event types broken |
| **Complexity** | Handles complex tool interactions well | Best for simple tool calls |
| **Compatibility** | Works with all models | Requires models with native tool calling |
| **Streaming** | Perfect for real-time updates | Poor - content gets overwritten |
| **Citations** | ✅ Full support | ✅ Full support |
| **Status Updates** | ✅ Full support | ✅ Full support |
| **Message Events** | ✅ Full support | ❌ Broken - content disappears |

**Decision Framework:**

1. **Do you need real-time content streaming, live updates, or dynamic message modification?**
   - **Yes** → Use **Default Mode** (Native mode will break these features)
   - **No** → Either mode works

2. **Is your tool primarily for simple data retrieval or computation?**
   - **Yes** → **Native Mode** is fine (lower latency)
   - **No** → Consider **Default Mode** for complex interactions

3. **Do you need maximum performance and minimal latency?**
   - **Yes** → **Native Mode** (if compatible with your features)
   - **No** → **Default Mode** provides more features

4. **Are you building interactive experiences, dashboards, or multi-step workflows?**
   - **Yes** → **Default Mode** required
   - **No** → Either mode works

**Recommended Usage Patterns:**

<details>
<summary>🏆 Best Practices for Mode Selection</summary>

**Choose Default Mode For:**
- Tools with progressive content updates
- Interactive dashboards or live data displays
- Multi-step workflows with visual feedback
- Complex tool chains with intermediate results
- Educational tools that show step-by-step processes
- Any tool that needs `message`, `replace`, or `chat:message` events

**Choose Native Mode For:**
- Simple API calls or database queries
- Basic calculations or data transformations
- Tools that only need status updates and citations
- Performance-critical applications where latency matters
- Simple retrieval tools without complex UI requirements

**Universal Compatibility Pattern:**
```python
async def mode_adaptive_tool(
        self, query: str, __event_emitter__=None, __metadata__=None
    ) -> str:
        """
        Tool that adapts its behavior based on function calling mode
        ✅ Provides optimal experience in both modes
        """

        # Detect current mode
        mode = "default"
        if __metadata__:
            mode = __metadata__.get("params", {}).get("function_calling", "default")

        is_native_mode = (mode == "native")

        if not __event_emitter__:
            return "Tool executed successfully (no event support)"

        # Always safe: status updates work in both modes
        await __event_emitter__({
            "type": "status",
            "data": {"description": f"Running in {mode} mode...", "done": False}
        })

        # Mode-specific logic
        if is_native_mode:
            # Native mode: use compatible events only
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Processing with native efficiency...", "done": False}
            })

            # Simulate processing
            await asyncio.sleep(1)

            # Return results directly - no message streaming
            result = f"Query '{query}' processed successfully in Native mode."

        else:
            # Default mode: full event capabilities
            await __event_emitter__({
                "type": "message",
                "data": {"content": f"🔍 **Processing Query**: {query}\n\n"}
            })

            await __event_emitter__({
                "type": "status",
                "data": {"description": "Analyzing with full streaming...", "done": False}
            })

            await asyncio.sleep(1)

            await __event_emitter__({
                "type": "message",
                "data": {"content": "📊 **Results**: Analysis complete with detailed findings.\n\n"}
            })

            result = "Query processed with full Default mode capabilities."

        # Final status (works in both modes)
        await __event_emitter__({
            "type": "status",
            "data": {"description": "Processing complete!", "done": True}
        })

        return result
```
</details>

<details>
<summary>🔧 Debugging Event Emitter Issues</summary>

**Common Issues and Solutions:**

**Issue: Content appears then disappears**
- **Cause**: Using message events in Native mode
- **Solution**: Switch to Default mode or use status events instead

**Issue: Tool seems unresponsive**
- **Cause**: Function calling not enabled for model
- **Solution**: Enable tools in Model settings or via `+` button

**Issue: Events not firing at all**
- **Cause**: `__event_emitter__` parameter missing or None
- **Solution**: Ensure parameter is included in tool method signature

**Issue: Citations being overwritten**
- **Cause**: `self.citation = True` (or not set to False)
- **Solution**: Set `self.citation = False` in `__init__` method

**Diagnostic Tool:**
```python
async def event_diagnostics_tool(
        self, __event_emitter__=None, __metadata__=None, __user__=None
    ) -> str:
        """
        Comprehensive diagnostic tool for event emitter debugging
        """

        report = ["# 🔍 Event Emitter Diagnostic Report\n"]

        # Check event emitter availability
        if __event_emitter__:
            report.append("✅ Event emitter is available\n")
        else:
            report.append("❌ Event emitter is NOT available\n")
            return "".join(report)

        # Check metadata availability
        if __metadata__:
            mode = __metadata__.get("params", {}).get("function_calling", "default")
            report.append(f"✅ Function calling mode: **{mode}**\n")
        else:
            report.append("⚠️ Metadata not available (mode unknown)\n")
            mode = "unknown"

        # Check user context
        if __user__:
            report.append("✅ User context available\n")
        else:
            report.append("⚠️ User context not available\n")

        # Test compatible events (work in both modes)
        report.append("\n## Testing Compatible Events:\n")

        try:
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Testing status events...", "done": False}
            })
            report.append("✅ Status events: WORKING\n")
        except Exception as e:
            report.append(f"❌ Status events: FAILED - {str(e)}\n")

        try:
            await __event_emitter__({
                "type": "notification",
                "data": {"content": "Test notification"}
            })
            report.append("✅ Notification events: WORKING\n")
        except Exception as e:
            report.append(f"❌ Notification events: FAILED - {str(e)}\n")

        # Test problematic events (broken in Native mode)
        report.append("\n## Testing Mode-Dependent Events:\n")

        try:
            await __event_emitter__({
                "type": "message",
                "data": {"content": "**Test message event** - This should appear in Default mode only\n"}
            })
            report.append("✅ Message events: SENT (may disappear in Native mode)\n")
        except Exception as e:
            report.append(f"❌ Message events: FAILED - {str(e)}\n")

        # Final status
        await __event_emitter__({
            "type": "status",
            "data": {"description": "Diagnostic complete", "done": True}
        })

        # Mode-specific recommendations
        report.append("\n## Recommendations:\n")

        if mode == "native":
            report.append("""
⚠️ **Native Mode Detected**: Limited event support
- ✅ Use: status, citation, notification, files events
- ❌ Avoid: message, replace, chat:message events
- 💡 Switch to Default mode for full functionality
""")
        elif mode == "default":
            report.append("""
✅ **Default Mode Detected**: Full event support available
- All event types should work perfectly
- Optimal for interactive and streaming tools
""")
        else:
            report.append("""
❓ **Unknown Mode**: Check your model configuration
- Ensure function calling is enabled
- Verify model supports tool calling
""")

        return "".join(report)
```
</details>

<details>
<summary>📚 Event Emitter Quick Reference</summary>

**Always Compatible (Both Modes):**
```python

# Status updates - perfect for progress tracking
await __event_emitter__({
    "type": "status",
    "data": {"description": "Processing...", "done": False}
})

# Citations - essential for source attribution
await __event_emitter__({
    "type": "citation",
    "data": {
        "document": ["Content"],
        "source": {"name": "Source", "url": "https://example.com"}
    }
})

# Notifications - user alerts
await __event_emitter__({
    "type": "notification",
    "data": {"content": "Task completed!"}
})
```

**Default Mode Only (Broken in Native):**
```python

# ⚠️ These will flicker/disappear in Native mode

# Progressive content streaming
await __event_emitter__({
    "type": "message",
    "data": {"content": "Streaming content..."}
})

# Content replacement
await __event_emitter__({
    "type": "replace",
    "data": {"content": "New complete content"}
})

# Delta updates
await __event_emitter__({
    "type": "chat:message:delta",
    "data": {"content": "Additional content"}
})
```

**Mode Detection Pattern:**
```python
def get_function_calling_mode(__metadata__):
    """Utility to detect current function calling mode"""
    if not __metadata__:
        return "unknown"
    return __metadata__.get("params", {}).get("function_calling", "default")

# Usage in tools:
mode = get_function_calling_mode(__metadata__)
is_native = (mode == "native")
can_stream_messages = not is_native
```

**Essential Imports:**
```python
import asyncio
from datetime import datetime
from typing import Optional, Callable, Awaitable
```
</details>

### Rich UI Element Embedding

Tools and Actions can return HTML content that renders as interactive iframes directly in the chat. For full documentation, examples, security considerations, and CORS configuration, see the dedicated **[Rich UI Embedding](/features/extensibility/plugin/development/rich-ui)** guide.


## External packages

In the Tools definition metadata you can specify custom packages. When you click `Save` the line will be parsed and `pip install` will be run on all requirements at once.

:::warning

**🚨 CRITICAL WARNING: Potential for Package Version Conflicts**

When multiple tools define different versions of the same package (e.g., Tool A requires `pandas==1.5.0` and Tool B requires `pandas==2.0.0`), Open WebUI installs them in a non-deterministic order. This can lead to unpredictable behavior and break one or more of your tools.

**The only robust solution to this problem is to use an OpenAPI tool server.**

We strongly recommend using an [OpenAPI tool server](/features/extensibility/plugin/tools/openapi-servers/) to avoid these dependency conflicts.

:::

:::danger Production / Multi-Worker Deployments

**Do not rely on runtime pip installation in production environments.** When running with `UVICORN_WORKERS > 1` or multiple replicas, each worker/replica attempts to install packages independently on startup. This causes **race conditions** where concurrent pip processes crash with `AssertionError` because pip's internal locking detects the simultaneous installs.

**Set [`ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS=False`](/reference/env-configuration#enable_pip_install_frontmatter_requirements) in production** to disable runtime pip installs entirely. Then pre-install all required packages at image build time using a custom Dockerfile:

```dockerfile
FROM ghcr.io/open-webui/open-webui:main

RUN pip install --no-cache-dir python-docx requests beautifulsoup4
```

Runtime installation is only suitable for **single-worker development or homelab environments** where you're experimenting with new functions and tools. For any deployment serving multiple users, bake your dependencies into the container image.

:::

:::note

Keep in mind that pip runs in the same process as Open WebUI, so the **UI will be completely unresponsive** during installation.

No measures are taken to handle package conflicts with Open WebUI's own dependencies. Specifying requirements can break Open WebUI if you're not careful. You might be able to work around this by specifying `open-webui` itself as a requirement.

:::

<details>
<summary>Example</summary>

```python
"""
title: myToolName
author: myName
funding_url: [any link here will be shown behind a `Heart` button for users to show their support to you]
version: 1.0.0

# the version is displayed in the UI to help users keep track of updates.
license: GPLv3
description: [recommended]
requirements: package1>=2.7.0,package2,package3
"""
```

</details>



---
sidebar_position: 3
title: "Events"
---

# 🔔 Events: Using `__event_emitter__` and `__event_call__` in Open WebUI

Open WebUI's plugin architecture is not just about processing input and producing output—**it's about real-time, interactive communication with the UI and users**. To make your Tools, Functions, and Pipes more dynamic, Open WebUI provides a built-in event system via the `__event_emitter__` and `__event_call__` helpers.

This guide explains **what events are**, **how you can trigger them** from your code, and **the full catalog of event types** you can use (including much more than just `"input"`).

---

## 🌊 What Are Events?

**Events** are real-time notifications or interactive requests sent from your backend code (Tool, or Function) to the web UI. They allow you to update the chat, display notifications, request confirmation, run UI flows, and more.

- Events are sent using the `__event_emitter__` helper for one-way updates, or `__event_call__` when you need user input or a response (e.g., confirmation, input, etc.).

**Metaphor:**
Think of Events like push notifications and modal dialogs that your plugin can trigger, making the chat experience richer and more interactive.

---

## 🏁 Availability

### Native Python Tools & Functions

Events are **fully available** for native Python Tools and Functions defined directly in Open WebUI using the `__event_emitter__` and `__event_call__` helpers.

### External Tools (OpenAPI & MCP)

External tools can emit events via a **dedicated REST endpoint**. Open WebUI passes the following headers to all external tool requests when `ENABLE_FORWARD_USER_INFO_HEADERS=True` is set:

| Header | Description |
|--------|-------------|
| `X-Open-WebUI-Chat-Id` | The chat ID where the tool was invoked |
| `X-Open-WebUI-Message-Id` | The message ID associated with the tool call |

Your external tool can use these headers to emit events back to the UI via:

```
POST /api/v1/chats/{chat_id}/messages/{message_id}/event
```

See [External Tool Events](#-external-tool-events) below for details.

---

## 🧰 Basic Usage

### Sending an Event

You can trigger an event anywhere inside your Tool, or Function by calling:

```python
await __event_emitter__(
    {
        "type": "status",  # See the event types list below
        "data": {
            "description": "Processing started!",
            "done": False,
            "hidden": False,
        },
    }
)
```

You **do not** need to manually add fields like `chat_id` or `message_id`—these are handled automatically by Open WebUI.

### Interactive Events

When you need to pause execution until the user responds (e.g., confirm/cancel dialogs, code execution, or input), use `__event_call__`:

```python
result = await __event_call__(
    {
        "type": "input",  # Or "confirmation", "execute"
        "data": {
            "title": "Please enter your password",
            "message": "Password is required for this action",
            "placeholder": "Your password here",
        },
    }
)

# result will contain the user's input value
```

:::tip Configurable Timeout
By default, `__event_call__` waits up to **300 seconds** (5 minutes) for a user response before timing out with an exception. This timeout is configurable via the [`WEBSOCKET_EVENT_CALLER_TIMEOUT`](/reference/env-configuration#websocket_event_caller_timeout) environment variable. Increase this value if your users need more time to fill out forms, make decisions, or complete complex interactions.
:::

---

## 📜 Event Payload Structure

When you emit or call an event, the basic structure is:

```json
{
  "type": "event_type",   // See full list below
  "data": { ... }         // Event-specific payload
}
```

Most of the time, you only set `"type"` and `"data"`. Open WebUI fills in the routing automatically.

---

## 🗂 Full List of Event Types

Below is a comprehensive table of **all supported `type` values** for events, along with their intended effect and data structure. (This is based on up-to-date analysis of Open WebUI event handling logic.)

| type                                         | When to use                                          | Data payload structure (examples)                                                                    |
| -------------------------------------------- | ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `status`                                     | Show a status update/history for a message           | `{description: ..., done: bool, hidden: bool}`                                                       |
| `chat:completion`                            | Provide a chat completion result                     | (Custom, see Open WebUI internals)                                                                   |
| `chat:message:delta`,<br/>`message`          | Append content to the current message                | `{content: "text to append"}`                                                                        |
| `chat:message`,<br/>`replace`                | Replace current message content completely           | `{content: "replacement text"}`                                                                      |
| `chat:message:files`,<br/>`files`            | Set or overwrite message files (for uploads, output) | `{files: [...]}`                                                                                     |
| `chat:title`                                 | Set (or update) the chat conversation title          | Topic string OR `{title: ...}`                                                                       |
| `chat:tags`                                  | Update the set of tags for a chat                    | Tag array or object                                                                                  |
| `source`,<br/>`citation`                     | Add a source/citation, or code execution result      | For code: See [below.](/features/extensibility/plugin/development/events#source-or-citation-and-code-execution) |
| `notification`                               | Show a notification ("toast") in the UI              | `{type: "info" or "success" or "error" or "warning", content: "..."}`                                |
| `confirmation` <br/>(needs `__event_call__`) | Ask for confirmation (OK/Cancel dialog)              | `{title: "...", message: "..."}`                                                                     |
| `input` <br/>(needs `__event_call__`)        | Request simple user input ("input box" dialog)       | `{title: "...", message: "...", placeholder: "...", value: ..., type: "password"}` (type is optional) |
| `execute` <br/>(`__event_call__` or `__event_emitter__`) | Run JavaScript in the user's browser. Use `__event_call__` to get a return value, or `__event_emitter__` for fire-and-forget | `{code: "...javascript code..."}`                                                                    |
| `chat:message:favorite`                      | Update the favorite/pin status of a message          | `{"favorite": bool}`                                                                                 |

**Other/Advanced types:**

- You can define your own types and handle them at the UI layer (or use upcoming event-extension mechanisms).

### ❗ Details on Specific Event Types

### `status`

Show a status/progress update in the UI:

```python
await __event_emitter__(
    {
        "type": "status",
        "data": {
            "description": "Step 1/3: Fetching data...",
            "done": False,
            "hidden": False,
        },
    }
)
```

#### The `done` Field

The `done` field controls the **shimmer animation** on the status text in the UI:

| `done` value | Visual effect |
|---|---|
| `false` (or omitted) | Status text has a **shimmer/loading animation** — indicates ongoing processing |
| `true` | Status text appears **static** — indicates the step is complete |

The backend does not inspect `done` at all — it simply saves the value and forwards it to the frontend. The shimmer effect is purely a frontend visual cue.

:::warning Always Emit a Final `done: True`
If you emit status events, always send at least one with `done: True` at the end of your status sequence. Without it, the last status item keeps its shimmer animation indefinitely, making it look like processing never finished — even after the response is complete.

```python
# ✅ Correct pattern
await __event_emitter__({"type": "status", "data": {"description": "Fetching data...", "done": False}})
# ... do work ...
await __event_emitter__({"type": "status", "data": {"description": "Complete!", "done": True}})

# ⚠️ Broken pattern — shimmer never stops
await __event_emitter__({"type": "status", "data": {"description": "Fetching data...", "done": False}})
# ... do work, return result, but never sent done: True
```
:::

#### The `hidden` Field

When `hidden` is `true`, the status is saved to `statusHistory` but **not shown** in the current status display. This is useful for internal status tracking that shouldn't be visible to the user.

Additionally, when `message.content` is empty and the last status has `hidden: true` (or no status exists at all), the frontend shows a skeleton loader instead of the status bar — so hidden statuses don't replace the loading indicator.

---

### `chat:message:delta` or `message`

**Streaming output** (append text):

```python
await __event_emitter__(
    {
        "type": "chat:message:delta",  # or simply "message"
        "data": {
            "content": "Partial text, "
        },
    }
)

# Later, as you generate more:
await __event_emitter__(
    {
        "type": "chat:message:delta",
        "data": {
            "content": "next chunk of response."
        },
    }
)
```

---

### `chat:message` or `replace`

**Set (or replace) the entire message content:**

```python
await __event_emitter__(
    {
        "type": "chat:message",  # or "replace"
        "data": {
            "content": "Final, complete response."
        },
    }
)
```

---

### `files` or `chat:message:files`

**Attach or update files:**

```python
await __event_emitter__(
    {
        "type": "files",  # or "chat:message:files"
        "data": {
            "files": [
               # Open WebUI File Objects
            ]
        },
    }
)
```

---

### `chat:title`

**Update the chat's title:**

```python
await __event_emitter__(
    {
        "type": "chat:title",
        "data": {
            "title": "Market Analysis Bot Session"
        },
    }
)
```

---

### `chat:tags`

**Update the chat's tags:**

```python
await __event_emitter__(
    {
        "type": "chat:tags",
        "data": {
            "tags": ["finance", "AI", "daily-report"]
        },
    }
)
```

---

### `source` or `citation` (and code execution)

**Add a reference/citation:**

```python
await __event_emitter__(
    {
        "type": "source",  # or "citation"
        "data": {
            # Open WebUI Source (Citation) Object
        }
    }
)
```

**For code execution (track execution state):**

```python
await __event_emitter__(
    {
        "type": "source",
        "data": {
            # Open WebUI Code Source (Citation) Object
        }
    }
)
```

---

### `notification`

**Show a toast notification:**

```python
await __event_emitter__(
    {
        "type": "notification",
        "data": {
            "type": "info",  # "success", "warning", "error"
            "content": "The operation completed successfully!"
        }
    }
)
```

---

### `chat:message:favorite`

**Update the favorite/pin status of a message:**

```python
await __event_emitter__(
    {
        "type": "chat:message:favorite",
        "data": {
            "favorite": True  # or False to unpin
        }
    }
)
```

**What this does exactly:**
This event forces the Open WebUI frontend to update the "favorite" state of a message in its local cache. Without this emitter, if an **Action Function** modifies the `message.favorite` field in the database directly, the frontend (which maintains its own state) might overwrite your change during its next auto-save cycle. This emitter ensures the UI and database stay perfectly in sync.

:::note Designed for Actions
While this event can technically be emitted from any plugin type (tools, pipes, filters), it is **designed for and meaningful in Actions**. Actions operate on existing messages and can modify the database directly. From a pipe or tool, emitting this event would update the frontend state temporarily, but unless the plugin also wrote to the database, the change would be lost on the next chat auto-save.
:::

**Where it appears:**
*   **Message Toolbar**: When set to `True`, the "Heart" icon beneath the message will fill in, indicating it is favorited.
*   **Chat Overview**: Favorited messages (pins) are highlighted in the conversation overview, making it easier for users to locate key information later.

#### Example: "Pin Message" Action
For a practical implementation of this event in a real-world plugin, see the **[Pin Message Action on Open WebUI Community](https://openwebui.com/posts/pin_message_action_143594d1)**. This action demonstrates how to toggle the favorite status in the database and immediately sync the UI using the `chat:message:favorite` event.

---

### `confirmation` (**requires** `__event_call__`)

**Show a confirm dialog and get user response:**

```python
result = await __event_call__(
    {
        "type": "confirmation",
        "data": {
            "title": "Are you sure?",
            "message": "Do you really want to proceed?"
        }
    }
)

if result:  # or check result contents
    await __event_emitter__({
        "type": "notification",
        "data": {"type": "success", "content": "User confirmed operation."}
    })
else:
    await __event_emitter__({
        "type": "notification",
        "data": {"type": "warning", "content": "User cancelled."}
    })
```

---

### `input` (**requires** `__event_call__`)

**Prompt user for text input:**

```python
result = await __event_call__(
    {
        "type": "input",
        "data": {
            "title": "Enter your name",
            "message": "We need your name to proceed.",
            "placeholder": "Your full name"
        }
    }
)

user_input = result
await __event_emitter__(
    {
        "type": "notification",
        "data": {"type": "info", "content": f"You entered: {user_input}"}
    }
)
```

#### Masked / Password Input

To hide sensitive input (e.g., API keys, passwords), set `type` to `"password"` in the data payload. The input field will be rendered as a masked password input with a show/hide toggle:

```python
result = await __event_call__(
    {
        "type": "input",
        "data": {
            "title": "Enter API Key",
            "message": "Your API key is required for this integration.",
            "placeholder": "sk-...",
            "type": "password"
        }
    }
)
```

:::tip
This uses the same `SensitiveInput` component used for user valve password fields, providing a familiar "eye" icon toggle for showing/hiding the value.
:::

---

### `execute` (works with both `__event_call__` and `__event_emitter__`)

**Run JavaScript directly in the user's browser.**

Unlike `confirmation` and `input`, the `execute` event works with **both** helpers:

| Helper | Behavior | Use when |
|---|---|---|
| `__event_call__` | Runs JS and **waits for the return value** (two-way) | You need the result back in Python (e.g., reading `localStorage`, detecting browser state) |
| `__event_emitter__` | Runs JS **fire-and-forget** (one-way) | You don't need the result (e.g., triggering a file download, manipulating the DOM) |

#### Two-way example (with `__event_call__`)

```python
result = await __event_call__(
    {
        "type": "execute",
        "data": {
            "code": "return document.title;",
        }
    }
)

await __event_emitter__(
    {
        "type": "notification",
        "data": {
            "type": "info",
            "content": f"Page title: {result}"
        }
    }
)
```

#### Fire-and-forget example (with `__event_emitter__`)

```python
# Trigger a blob download — no return value needed
try:
    await __event_emitter__(
        {
            "type": "execute",
            "data": {
                "code": """
                    (function() {
                        const blob = new Blob([data], {type: 'application/octet-stream'});
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = 'file.bin';
                        document.body.appendChild(a);
                        a.click();
                        URL.revokeObjectURL(url);
                        a.remove();
                    })();
                """
            }
        }
    )
except Exception:
    pass
```

:::tip iOS PWA compatibility
On iOS Safari (especially in PWA / standalone mode), using `__event_call__` for blob downloads can fail with a `"TypeError: Load failed"` error — the two-way response channel breaks when the browser processes the download. Using `__event_emitter__` (fire-and-forget) avoids this issue entirely, since no response channel is needed.

If your `execute` code triggers a file download and you don't need a return value, prefer `__event_emitter__` for maximum cross-platform compatibility.
:::

#### How It Works

The `execute` event runs JavaScript **directly in the main page context** using `new Function()`. This means:

- It runs with **full access** to the page's DOM, cookies, localStorage, and session
- It is **not sandboxed** — there are no iframe restrictions
- It can manipulate the Open WebUI interface directly (show/hide elements, read form data, trigger downloads)
- The code runs as an async function, so you can use `await` and `return` a value back to the backend (when using `__event_call__`)

:::tip Frontend Automation
Because `execute` runs in the main page context with full DOM access, you can use it to **automate virtually anything on the Open WebUI frontend**: click buttons, fill input fields, navigate between pages, read page state, trigger downloads, interact with the model selector, submit messages on behalf of the user, and more. Think of it as a remote control for the browser UI — if a user can do it manually, your function can do it programmatically via `execute`.
:::

#### Example: Display a Custom Form

```python
result = await __event_call__(
    {
        "type": "execute",
        "data": {
            "code": """
                return new Promise((resolve) => {
                    const overlay = document.createElement('div');
                    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999';
                    overlay.innerHTML = `
                        <div style="background:white;padding:24px;border-radius:12px;min-width:300px">
                            <h3 style="margin:0 0 12px">Enter Details</h3>
                            <input id="exec-name" placeholder="Name" style="width:100%;padding:8px;margin:4px 0;border:1px solid #ccc;border-radius:6px"/>
                            <input id="exec-email" placeholder="Email" style="width:100%;padding:8px;margin:4px 0;border:1px solid #ccc;border-radius:6px"/>
                            <button id="exec-submit" style="margin-top:12px;padding:8px 16px;background:#333;color:white;border:none;border-radius:6px;cursor:pointer">Submit</button>
                        </div>
                    `;
                    document.body.appendChild(overlay);
                    document.getElementById('exec-submit').onclick = () => {
                        const name = document.getElementById('exec-name').value;
                        const email = document.getElementById('exec-email').value;
                        overlay.remove();
                        resolve({ name, email });
                    };
                });
            """
        }
    }
)
# result will be {"name": "...", "email": "..."}
```

#### Execute vs Rich UI Embeds

The `execute` event and [Rich UI Embeds](/features/extensibility/plugin/development/rich-ui) are complementary ways to create interactive experiences:

| | `execute` Event | Rich UI Embed |
|---|---|---|
| **Runs in** | Main page context (no sandbox) | Sandboxed iframe |
| **Persistence** | Ephemeral — gone on reload/navigate | Persistent — saved in chat history |
| **Page access** | Full (DOM, cookies, localStorage) | Isolated from parent by default |
| **Forms** | Always works (no sandbox) | Requires `allowForms` setting enabled |
| **Best for** | Transient interactions, side effects, downloads, DOM manipulation | Persistent visual content, dashboards, charts |

Use `execute` for transient interactions (confirmations, custom dialogs, triggering downloads, reading page state) and Rich UI embeds for persistent visual content you want to stay in the conversation.

:::warning
Because `execute` runs unsandboxed JavaScript in the user's browser session, it has full access to the Open WebUI page context. Only use this in trusted functions — never execute untrusted or user-provided code through this event.
:::

---

## 🏗️ When & Where to Use Events

- **From any Tool, or Function** in Open WebUI.
- To **stream responses**, show progress, request user data, update the UI, or display supplementary info/files.
- `await __event_emitter__` is for one-way messages (fire and forget).
- `await __event_call__` is for when you need a response from the user (input, confirmation) or a return value from client-side code (execute).
- The `execute` event is unique: it works with **both** helpers. Use `__event_call__` when you need the JS return value, or `__event_emitter__` for fire-and-forget execution (e.g., triggering downloads).

:::warning Pipes: Return Value vs Events
For Pipes, be careful about mixing content delivery methods. If your `pipe()` method **returns a string**, that string becomes the final message content. If it **yields** (generator), the yielded chunks are streamed. If you also emit `chat:message:delta` events during execution, both the return/yield content and the event-based content are processed and can conflict.

**Recommendation**: Use return/yield as your primary content delivery mechanism. Events like `status`, `source`, `files`, and `notification` work well alongside return/yield, but avoid using `chat:message:delta` or `chat:message` events as your **sole** way to deliver message content from a pipe.

**Why event-only content delivery is fragile for pipes**: When a pipe completes, the frontend saves the entire chat history (including all message content from its local state) to the database. This full-history save can **overwrite** content that was previously persisted by the backend event emitter. If the pipe returns `None` or an empty string and relies solely on `type: "message"` events for content, the final save may write empty content to the database — erasing what the event emitter had written.

```python
# ❌ Fragile: relies only on events for content — can be overwritten on save
async def pipe(self, body: dict, __event_emitter__=None):
    await __event_emitter__({"type": "message", "data": {"content": "Hello!"}})
    # Returns None — frontend may save empty content, overwriting the emitted content

# ✅ Correct: return content directly, use events for supplementary data
async def pipe(self, body: dict, __event_emitter__=None):
    await __event_emitter__({"type": "status", "data": {"description": "Working...", "done": False}})
    result = "Hello!"
    await __event_emitter__({"type": "status", "data": {"description": "Done", "done": True}})
    return result

# ✅ Also correct: yield for streaming, use events for supplementary data
async def pipe(self, body: dict, __event_emitter__=None):
    await __event_emitter__({"type": "status", "data": {"description": "Streaming...", "done": False}})
    for chunk in ["Hello", ", ", "world", "!"]:
        yield chunk
    await __event_emitter__({"type": "status", "data": {"description": "Done", "done": True}})
```
:::

---

## 💡 Tips & Advanced Notes

- **Multiple types per message:** You can emit several events of different types for one message—for example, show `status` updates, then stream with `chat:message:delta`, then complete with a `chat:message`.
- **Custom event types:** While the above list is the standard, you may use your own types and detect/handle them in custom UI code.
- **Extensibility:** The event system is designed to evolve—always check the [Open WebUI documentation](https://github.com/open-webui/open-webui) for the most current list and advanced usage.

---

## 🧐 FAQ

### Q: How do I trigger a notification for the user?
Use `notification` type:
```python
await __event_emitter__({
    "type": "notification",
    "data": {"type": "success", "content": "Task complete"}
})
```

### Q: How do I prompt the user for input and get their answer?
Use:
```python
response = await __event_call__({
    "type": "input",
    "data": {
        "title": "What's your name?",
        "message": "Please enter your preferred name:",
        "placeholder": "Name"
    }
})

# response will be: {"value": "user's answer"}
```

### Q: What event types are available for `__event_call__`?
- `"input"`: Input box dialog
- `"confirmation"`: Yes/No, OK/Cancel dialog
- `"execute"`: Run provided code on client and return result (also works with `__event_emitter__` for fire-and-forget — see [execute](#execute-works-with-both-__event_call__-and-__event_emitter__) above)

### Q: Can I update files attached to a message?
Yes—use the `"files"` or `"chat:message:files"` event type with a `{files: [...]}` payload.

### Q: Can I update the conversation title or tags?
Absolutely: use `"chat:title"` or `"chat:tags"` accordingly.

### Q: Can I stream responses (partial tokens) to the user?
Yes—emit `"chat:message:delta"` events in a loop, then finish with `"chat:message"`.

---

## 🌐 External Tool Events

External tools (OpenAPI and MCP servers) can emit events to the Open WebUI UI via a REST endpoint. This enables features like status updates, notifications, and streaming content from tools running on external servers.

### Prerequisites

To receive the chat and message ID headers, you must enable header forwarding by setting the following environment variable on your Open WebUI instance:

```
ENABLE_FORWARD_USER_INFO_HEADERS=True
```

Without this, Open WebUI will not include the identification headers in requests to external tools, and event emitting will not work.

### Headers Provided by Open WebUI

When Open WebUI calls your external tool (with header forwarding enabled), it includes these headers:

| Header | Description | Env Var Override |
|--------|-------------|------------------|
| `X-Open-WebUI-Chat-Id` | The chat ID where the tool was invoked | `FORWARD_SESSION_INFO_HEADER_CHAT_ID` |
| `X-Open-WebUI-Message-Id` | The message ID associated with the tool call | `FORWARD_SESSION_INFO_HEADER_MESSAGE_ID` |

### Event Endpoint

**Endpoint:** `POST /api/v1/chats/{chat_id}/messages/{message_id}/event`

**Authentication:** Requires a valid Open WebUI API key or session token.

**Request Body:**

```json
{
  "type": "status",
  "data": {
    "description": "Processing your request...",
    "done": false
  }
}
```

### Supported Event Types

External tools can emit the same event types as native tools:
- `status` – Show progress/status updates
- `notification` – Display toast notifications
- `chat:message:delta` / `message` – Append content to the message
- `chat:message` / `replace` – Replace message content
- `files` / `chat:message:files` – Attach files
- `source` / `citation` – Add citations

:::note
Interactive events (`input`, `confirmation`) require `__event_call__` and are **not supported** for external tools as they need bidirectional WebSocket communication. `execute` via `__event_call__` is similarly unsupported for external tools; however, fire-and-forget `execute` via `__event_emitter__` does not require a return channel and may work depending on your setup.
:::

### Example: Python External Tool

```python
import httpx

def my_tool_handler(request):
    # Extract headers from incoming request
    chat_id = request.headers.get("X-Open-WebUI-Chat-Id")
    message_id = request.headers.get("X-Open-WebUI-Message-Id")
    api_key = "your-open-webui-api-key"
    
    # Emit a status event
    httpx.post(
        f"http://your-open-webui-host/api/v1/chats/{chat_id}/messages/{message_id}/event",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "type": "status",
            "data": {"description": "Working on it...", "done": False}
        }
    )
    
    # ... do work ...
    
    # Emit completion status
    httpx.post(
        f"http://your-open-webui-host/api/v1/chats/{chat_id}/messages/{message_id}/event",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "type": "status",
            "data": {"description": "Complete!", "done": True}
        }
    )
    
    return {"result": "success"}
```

### Example: JavaScript/Node.js External Tool

```javascript
async function myToolHandler(req) {
  const chatId = req.headers['x-open-webui-chat-id'];
  const messageId = req.headers['x-open-webui-message-id'];
  const apiKey = 'your-open-webui-api-key';
  
  // Emit a notification
  await fetch(
    `http://your-open-webui-host/api/v1/chats/${chatId}/messages/${messageId}/event`,
    {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        type: 'notification',
        data: { type: 'info', content: 'Tool is processing...' }
      })
    }
  );
  
  return { result: 'success' };
}
```

---

## 🔒 Persistence & Browser Disconnection

A common question is: **what happens if the browser tab is closed while a tool, action, or pipe is still running?**

### Server-Side Execution Continues

When you send a chat request, Open WebUI creates a background `asyncio` task that is **not tied to your HTTP connection or Socket.IO session**. If you close the tab:

1. The WebSocket disconnects and the Socket.IO disconnect handler fires
2. The disconnect handler cleans up session data but **does not cancel any running tasks**
3. The background task continues running to completion on the server
4. `sio.emit()` calls succeed silently — events are sent to an empty room and discarded
5. **Database writes still happen** for persisted event types (see below)
6. The task runs until the function returns, raises an error, or is manually cancelled

:::info No Execution Timeout
There is **no timeout** on pipe, tool, or action execution. Your code can run for minutes or hours — nothing in Open WebUI will kill it automatically. The only things that can stop a running task are:
- The function itself returning or raising an exception
- Manual cancellation via `POST /api/tasks/stop/{task_id}` (the stop button in the UI)
- The Open WebUI server process restarting
:::

### Which Event Types Are Persisted to the Database?

The event emitter writes certain event types directly to the database **regardless of whether a browser is connected**. These writes are independent of the `ENABLE_REALTIME_CHAT_SAVE` setting.

#### ✅ Persisted (survive tab close)

| Type | What's saved |
|------|-------------|
| `status` | Appended to the message's `statusHistory` array |
| `message` | Appended to the message's `content` field |
| `replace` | Overwrites the message's `content` field |
| `embeds` | Appended to the message's `embeds` array (Rich UI HTML) |
| `files` | Appended to the message's `files` array |
| `source` / `citation` | Appended to the message's `sources` array |

These 6 types always write to the database inside the event emitter function itself, completely independent of `ENABLE_REALTIME_CHAT_SAVE`.

:::warning Use Short Names for Persistence
The backend event emitter only recognizes the short names above for DB writes. If you emit `"chat:message:embeds"` instead of `"embeds"`, the frontend handles it identically, but the **backend won't persist it**. Always use the short names (`"status"`, `"message"`, `"replace"`, `"embeds"`, `"files"`, `"source"`) if you need persistence.
:::

:::caution Pipes: Backend Persistence Can Be Overwritten
For **Pipes specifically**, the backend-persisted content from `"message"` and `"replace"` events can be **overwritten** by the frontend after the pipe completes. When a pipe's `pipe()` method returns, the frontend saves the entire local chat history to the database. If the pipe returned `None` or empty content and relied solely on `"message"` events, the frontend's local state may still have empty content for the assistant message — causing it to overwrite the event-emitter-written content with an empty string.

This does **not** affect Tools, Actions, or Filters, where events supplement the return value rather than replace it. It also does not affect `"status"`, `"files"`, `"source"`, or `"embeds"` events, which update separate fields that aren't overwritten by the content save.

**Bottom line for Pipes**: Use return/yield for message content. Use events for status updates, sources, files, embeds, and notifications.
:::

#### ❌ Not persisted (lost on tab close)

| Type | Why it's lost |
|------|--------------|
| `chat:completion` | Streaming LLM deltas — Socket.IO only |
| `chat:message:delta` | Frontend alias, backend doesn't persist |
| `chat:message` | Frontend alias, backend doesn't persist |
| `chat:message:files` | Frontend alias, backend doesn't persist |
| `chat:message:embeds` | Frontend alias, backend doesn't persist |
| `chat:message:error` | Socket.IO only |
| `chat:message:follow_ups` | Socket.IO only |
| `chat:message:favorite` | Socket.IO only (updates frontend state) |
| `chat:title` | Socket.IO only |
| `chat:tags` | Socket.IO only |
| `notification` | Toast popup — Socket.IO only |

:::tip Alternative for Streaming LLM Output
If your pipe or tool needs to call an LLM and have the result persist even when the browser is closed, you can import and use `generate_chat_completion` from Open WebUI's internals instead of emitting `chat:completion` events. The completion flows through the normal chat pipeline and its result is saved to the database like any other assistant message.
:::

#### ⚠️ Requires live connection (will error on tab close)

| Type | Why |
|------|-----|
| `confirmation` | Uses `sio.call()` — waits for client response, will timeout |
| `input` | Uses `sio.call()` — waits for client response, will timeout |
| `execute` via `__event_call__` | Uses `sio.call()` — waits for client response, will timeout |
| `execute` via `__event_emitter__` | Fires and forgets — **will not error**, but JS may not run if no browser is connected |

`confirmation` and `input` fundamentally require a live browser connection via `__event_call__`. If the tab is closed, `sio.call()` will timeout and raise an exception in your function code. The timeout is configurable via the [`WEBSOCKET_EVENT_CALLER_TIMEOUT`](/reference/env-configuration#websocket_event_caller_timeout) environment variable (default: 300 seconds).

`execute` is more flexible: when used via `__event_emitter__`, it fires without waiting for a response, so it won't error on tab close (though the JS won't execute if no browser is listening). This makes `__event_emitter__` the safer choice for `execute` calls where you don't need the return value — particularly for file downloads on iOS PWA, where the two-way channel can fail with `"TypeError: Load failed"`.

### Return Value Persistence

The final return value of your function is **always saved to the database** when the task completes, regardless of browser state.

#### Pipes

When a pipe's `pipe()` method returns (or its generator finishes yielding), the streaming handler saves the final result at completion:
- If `ENABLE_REALTIME_CHAT_SAVE` is **on**: intermediate chunks are saved during streaming
- If `ENABLE_REALTIME_CHAT_SAVE` is **off**: the full final content is saved in one write at completion

Either way, the final assistant message is always persisted. When you reopen the chat, it will be there.

:::caution
The return value takes precedence over event-emitted content. If your pipe emits `"message"` events but returns `None`, the saved content will be empty — the frontend's final save overwrites whatever the event emitter wrote to the database. Always return or yield your content directly from the `pipe()` method.
:::

#### Tools

| Return type | What happens | Persisted? |
|-------------|-------------|-----------|
| `HTMLResponse` (with `Content-Disposition: inline`) | HTML body extracted → added to `embeds` → emitted as `"embeds"` event | ✅ Yes |
| `HTMLResponse` (without inline) | Body decoded as plain text tool result | ✅ Yes |
| `str` / `dict` | Used as tool result text | ✅ Yes |
| `list` (MCP) | Text items joined, images converted to files | ✅ Yes |

#### Actions

Actions go through the same return handling as tools. The same persistence rules apply:

| Return type | What happens | Persisted? |
|-------------|-------------|-----------|
| `HTMLResponse` (with `Content-Disposition: inline`) | HTML body extracted → added to `embeds` → emitted as `"embeds"` event | ✅ Yes |
| `HTMLResponse` (without inline) | Body decoded as plain text result | ✅ Yes |
| `str` / `dict` | Used as action result text | ✅ Yes |

#### Filters

Filters transform `form_data` in the pipeline — they don't return results to the user directly. However, filters **do receive `__event_emitter__`** and can emit persisted event types like `"status"`, `"embeds"`, `"message"`, etc.

### Function Type Capabilities Matrix

| Capability | Tools | Actions | Pipes | Filters |
|-----------|-------|---------|-------|---------|
| `__event_emitter__` | ✅ | ✅ | ✅ | ✅ |
| `__event_call__` | ✅ | ✅ | ✅ | ✅ |
| Return value → user response | ✅ | ✅ | ✅ | ❌ (modifies `form_data`) |
| `HTMLResponse` → Rich UI embed | ✅ | ✅ | ❌ | ❌ |

### Practical Summary

If you want your function's output to **survive a closed browser tab**, follow these rules:

1. **Always return your final answer** from your `pipe()`, tool, or action function — the return value is always saved
2. **Use short event type names** (`"status"`, `"message"`, `"embeds"`, `"files"`, `"source"`) for DB persistence
3. **Avoid relying on** `"notification"`, `"confirmation"`, `"input"`, or `"execute"` for critical workflows — these require a live browser connection
4. Rich UI HTML embeds (`"embeds"` type or `HTMLResponse` return) **are persisted** and will render when the user reopens the chat

---

## 📝 Conclusion

**Events** give you real-time, interactive superpowers inside Open WebUI. They let your code update content, trigger notifications, request user input, stream results, handle code, and much more—seamlessly plugging your backend intelligence into the chat UI.

- Use `__event_emitter__` for one-way status/content updates.
- Use `__event_call__` for interactions that require user follow-up (input, confirmation, execution).

Refer to this document for common event types and structures, and explore Open WebUI source code or docs for breaking updates or custom events!

---

**Happy event-driven coding in Open WebUI! 🚀**

---
sidebar_position: 3
title: "Valves"
---

## Valves

Valves and UserValves are used to allow users to provide dynamic details such as an API key or a configuration option. These will create a fillable field or a bool switch in the GUI menu for the given function. They are always optional, but HIGHLY encouraged.

Hence, Valves and UserValves class can be defined in either a `Pipe`, `Pipeline`, `Filter` or `Tools` class.

Valves are configurable by admins alone via the Tools or Functions menus. On the other hand UserValves are configurable by any users directly from a chat session.

<details>
<summary>Commented example</summary>

```python

from pydantic import BaseModel, Field
from typing import Literal

# Define and Valves
class Filter:
   # Notice the current indentation: Valves and UserValves must be declared as
   # attributes of a Tools, Filter or Pipe class. Here we take the
   # example of a Filter.
    class Valves(BaseModel):
       # Valves and UserValves inherit from pydantic's BaseModel. This
       # enables complex use cases like model validators etc.
       test_valve: int = Field(  # Notice the type hint: it is used to
           # choose the kind of UI element to show the user (buttons,
           # texts, etc).
           default=4,
           description="A valve controlling a numberical value"
           # required=False,  # you can enforce fields using True
       )
       # To give the user the choice between multiple strings, you can use Literal from typing:
       choice_option: Literal["choiceA", "choiceB"] = Field(
           default="choiceA",
           description="An example of a multi choice valve",
       )
       priority: int = Field(
           default=0,
           description="Priority level for the filter operations. Lower values are passed through first"
       )
       # The priority field is optional but if present will be used to
       # order the Filters.
       pass
       # Note that this 'pass' helps for parsing and is recommended.

   # UserValves are defined the same way.
    class UserValves(BaseModel):
        test_user_valve: bool = Field(
            default=False, description="A user valve controlling a True/False (on/off) switch"
       )
       pass

   def __init__(self):
       self.valves = self.Valves()
       # Because they are set by the admin, they are accessible directly
       # upon code execution.
       pass

   # The inlet method is only used for Filter but the __user__ handling is the same
   def inlet(self, body: dict, __user__: dict):
       # Because UserValves are defined per user they are only available
       # on use.
       # Note that although __user__ is a dict, __user__["valves"] is a
       # UserValves object. Hence you can access values like that:
       test_user_valve = __user__["valves"].test_user_valve
       # Or:
       test_user_valve = dict(__user__["valves"])["test_user_valve"]
       # But this will return the default value instead of the actual value:
       # test_user_valve = __user__["valves"]["test_user_valve"]  # Do not do that!
```

</details>

## Input Types

Valves support special input types that change how fields are rendered in the UI. You can configure these using `json_schema_extra` with the `input` key in your Pydantic `Field` definitions.

### Password Input (Masked Fields)

For sensitive fields like passwords, API keys, or secrets, you can use the password input type to mask the value in the UI. This prevents the password from being visible on screen (protecting against shoulder surfing).

```python
from pydantic import BaseModel, Field

class Tools:
    class UserValves(BaseModel):
        service_password: str = Field(
            default="",
            description="Your service password",
            json_schema_extra={"input": {"type": "password"}}
        )
```

When rendered, this field will appear as a masked input (dots instead of characters) with a toggle to reveal the value if needed, using Open WebUI's `SensitiveInput` component.

:::tip
Use password input types for any credential or secret that users configure in their Valves or UserValves. This is especially important for UserValves since they are configurable by end users directly from the chat interface.
:::

### Select Dropdown Input

For fields where users should choose from a predefined list of options, use the select input type to render a dropdown menu. Options can be either static (hardcoded list) or dynamic (generated at runtime by a method).

#### Static Options

Use a list directly for options that don't change:

```python
from pydantic import BaseModel, Field

class Tools:
    class Valves(BaseModel):
        priority: str = Field(
            default="medium",
            description="Processing priority level",
            json_schema_extra={
                "input": {
                    "type": "select",
                    "options": ["low", "medium", "high"]
                }
            }
        )
```

You can also use label/value pairs for more descriptive options:

```python
from pydantic import BaseModel, Field

class Tools:
    class Valves(BaseModel):
        log_level: str = Field(
            default="info",
            description="Logging verbosity",
            json_schema_extra={
                "input": {
                    "type": "select",
                    "options": [
                        {"value": "debug", "label": "Debug (Verbose)"},
                        {"value": "info", "label": "Info (Standard)"},
                        {"value": "warn", "label": "Warning (Minimal)"},
                        {"value": "error", "label": "Error (Critical Only)"}
                    ]
                }
            }
        )
```

#### Dynamic Options

For options that need to be generated at runtime (e.g., fetching available models, databases, or user-specific resources), specify a method name as a string. The method will be called when the configuration UI is rendered:

```python
from pydantic import BaseModel, Field

class Tools:
    class Valves(BaseModel):
        selected_model: str = Field(
            default="",
            description="Choose a model to use",
            json_schema_extra={
                "input": {
                    "type": "select",
                    "options": "get_model_options"  # Method name as string
                }
            }
        )

        @classmethod
        def get_model_options(cls, __user__=None) -> list[dict]:
            """
            Dynamically fetch available models.
            Called when the Valves configuration UI is opened.
            """
            # Example: Return options based on runtime state
            return [
                {"value": "gpt-4", "label": "GPT-4"},
                {"value": "gpt-3.5-turbo", "label": "GPT-3.5 Turbo"},
                {"value": "claude-3-opus", "label": "Claude 3 Opus"}
            ]
```

The method can accept an optional `__user__` parameter to generate user-specific options:

```python
from pydantic import BaseModel, Field

class Tools:
    class UserValves(BaseModel):
        workspace: str = Field(
            default="",
            description="Select your workspace",
            json_schema_extra={
                "input": {
                    "type": "select",
                    "options": "get_user_workspaces"
                }
            }
        )

        @classmethod
        def get_user_workspaces(cls, __user__=None) -> list[dict]:
            """
            Return workspaces available to the current user.
            __user__ contains the user's information as a dict.
            """
            if not __user__:
                return []

            user_id = __user__.get("id")
            # Fetch user-specific workspaces from your data source
            return [
                {"value": "ws-1", "label": "Personal Workspace"},
                {"value": "ws-2", "label": "Team Workspace"}
            ]
```

:::tip
Dynamic options are particularly useful for:
- Fetching available API models from connected providers
- Loading database or file options based on current system state
- Presenting user-specific resources like projects or workspaces
- Any scenario where options change based on runtime context
:::

---
sidebar_position: 4
title: "Rich UI Embedding"
---

# Rich UI Element Embedding

Tools and Actions both support rich UI element embedding, allowing them to return HTML content and interactive iframes that display directly within chat conversations. This feature enables sophisticated visual interfaces, interactive widgets, charts, dashboards, and other rich web content — regardless of whether the function was triggered by the model (Tool) or by the user (Action).

When a function returns an `HTMLResponse` with the appropriate headers, the content will be embedded as an interactive iframe in the chat interface rather than displayed as plain text.

## Tool Usage

To embed HTML content, your tool should return an `HTMLResponse` with the `Content-Disposition: inline` header:

```python
from fastapi.responses import HTMLResponse

def create_visualization_tool(self, data: str) -> HTMLResponse:
    """
    Creates an interactive data visualization that embeds in the chat.

    :param data: The data to visualize
    """
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Data Visualization</title>
        <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    </head>
    <body>
        <div id="chart" style="width:100%;height:400px;"></div>
        <script>
            // Your interactive chart code here
            Plotly.newPlot('chart', [{
                y: [1, 2, 3, 4],
                type: 'scatter'
            }]);
        </script>
    </body>
    </html>
    """

    headers = {"Content-Disposition": "inline"}
    return HTMLResponse(content=html_content, headers=headers)
```

## Action Usage

Actions work exactly the same way. The rich UI embed is delivered to the chat via the event emitter:

**Option A — HTMLResponse:**

```python
from fastapi.responses import HTMLResponse

async def action(self, body, __event_emitter__=None):
    html = "<html><body><h1>Dashboard</h1></body></html>"
    return HTMLResponse(content=html, headers={"Content-Disposition": "inline"})
```

**Option B — Tuple with headers:**

```python
async def action(self, body, __event_emitter__=None):
    html = "<h1>Interactive Chart</h1><script>...</script>"
    return (html, {"Content-Disposition": "inline", "Content-Type": "text/html"})
```

## Iframe Height and Auto-Sizing

Rich UI embeds are rendered inside a sandboxed iframe. The iframe needs to know how tall its content is in order to display without scrollbars. There are two mechanisms for this:

### postMessage Height Reporting (Recommended)

When `allowSameOrigin` is **off** (the default), the parent page cannot read the iframe's content height directly. Your HTML must report its own height by posting a message to the parent window:

```html
<script>
  function reportHeight() {
    const h = document.documentElement.scrollHeight;
    parent.postMessage({ type: 'iframe:height', height: h }, '*');
  }
  window.addEventListener('load', reportHeight);
  // Also re-report when content changes size
  new ResizeObserver(reportHeight).observe(document.body);
</script>
```

Add this script to the end of your `<body>` in every Rich UI embed. Without it, the iframe will stay at a small default height and your content will be cut off with a scrollbar.

### Same-Origin Auto-Resize

When `allowSameOrigin` is **on** (via the user setting `iframeSandboxAllowSameOrigin`), the parent page can directly measure the iframe's content height and resize it automatically — no script needed in your HTML. However, this comes with security trade-offs (see below).

## Sandbox and Security

Embedded iframes run inside a [sandbox](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/iframe#sandbox). The following sandbox flags are always enabled by default:

- `allow-scripts` — JavaScript execution
- `allow-popups` — Popups (e.g. window.open)
- `allow-downloads` — File downloads

Two additional flags can be toggled by the user in **Settings → Interface**:

| Setting | Default | Description |
|---|---|---|
| Allow Iframe Same-Origin Access | ❌ Off | Allows the iframe to access parent page context |
| Allow Iframe Form Submissions | ❌ Off | Allows form submissions within embedded content |

### allowSameOrigin

This is the most important flag to be aware of. It is **off by default** for security reasons.

**When off (default):**
- The iframe is fully isolated from the parent page
- It **cannot** read cookies, localStorage, or DOM of the parent
- The parent **cannot** read the iframe's content height (so you must use the postMessage pattern above)
- This is the safest option and recommended for most use cases

**When on:**
- The iframe can interact with the parent page's context
- Auto-resizing works without any script in your HTML
- Chart.js and Alpine.js dependencies are automatically injected if detected
- ⚠️ **Use with caution** — only enable this when you trust the embedded content

Users can toggle this setting in **Settings → Interface → Iframe Same-Origin Access**.

:::caution Practical Impact of Sandbox Settings
When `allowSameOrigin` is **off** (default), the Rich UI iframe is heavily sandboxed. This means:
- **Downloads from within the embed** are difficult or impossible — especially on iOS, where sandboxed iframes cannot trigger file downloads at all
- **JavaScript in the embed cannot interact with Open WebUI itself** — the iframe has no access to the parent page's DOM, cookies, localStorage, or any Open WebUI APIs
- **Cross-frame communication** is limited to `postMessage` only

If your Rich UI embed needs to trigger downloads, interact with Open WebUI's frontend, or execute JavaScript that impacts the parent page, **enabling same-origin iframe access is required**. Enable it in **Settings → Interface → Iframe Same-Origin Access**.

As an alternative for ephemeral interactions that need full page access, consider using the [`execute` event](/features/extensibility/plugin/development/events#execute-requires-__event_call__) instead, which runs unsandboxed in the main page context.
:::

## Rendering Position

- **Tool embeds** inside a tool call result render **inline** at the tool call indicator (the "View Result from..." line)
- **Action embeds** and message-level embeds render **below** the message text content

## Advanced Communication

The iframe and parent window can communicate beyond just height reporting. The following patterns are available:

### Payload Requests

The iframe can request a data payload from the parent. This is useful for passing dynamic data into the embed after it loads:

```html
<script>
  // Request payload from parent
  window.addEventListener('message', (e) => {
    if (e.data?.type === 'payload') {
      const data = e.data.payload;
      // Use the payload data to populate your UI
      console.log('Received payload:', data);
    }
  });

  // Trigger the request
  parent.postMessage({ type: 'payload', requestId: 'my-request' }, '*');
</script>
```

The parent responds with `{ type: 'payload', requestId: ..., payload: ... }` containing the configured payload data.

### Tool Args Injection (Tools Only)

When a **Tool** returns a Rich UI embed, the tool call arguments (the parameters the model passed to the tool) are automatically injected into the iframe's `window.args`. This allows your embedded HTML to access the tool's input:

```html
<script>
  window.addEventListener('load', () => {
    // window.args contains the JSON arguments the model passed to this tool
    const args = window.args;
    if (args) {
      document.getElementById('output').textContent = JSON.stringify(args, null, 2);
    }
  });
</script>
```

:::note
This only works for Tool embeds rendered via the tool call display. Action embeds do not have `window.args` since they are triggered by the user, not the model.
:::

### Auto-Injected Libraries

When `allowSameOrigin` is enabled, the iframe component auto-detects usage of certain libraries in your HTML and injects them automatically — no CDN `<script>` tags needed:

- **Alpine.js** — Detected when any `x-data`, `x-init`, `x-show`, `x-bind`, `x-on`, `x-text`, `x-html`, `x-model`, `x-for`, `x-if`, `x-effect`, `x-transition`, `x-cloak`, `x-ref`, `x-teleport`, or `x-id` directives are found
- **Chart.js** — Detected when `new Chart(` or `Chart.` appears in the HTML

This means you can write Alpine or Chart.js code directly in your HTML and it will just work when same-origin is enabled, without importing scripts.

### Ping/Pong Connectivity

The iframe can test connectivity with the parent window using a simple ping/pong pattern:

```html
<script>
  window.addEventListener('message', (e) => {
    if (e.data?.type === 'pong:ack') {
      console.log('Parent is listening!');
    }
  });

  // Send a pong to test connectivity
  parent.postMessage({ type: 'pong' }, '*');
</script>
```

## Rich UI Embeds vs Execute Event

Rich UI embeds and the [`execute` event](/features/extensibility/plugin/development/events#execute-requires-__event_call__) are complementary ways to create interactive experiences. Choose based on your needs:

| | Rich UI Embed | `execute` Event |
|---|---|---|
| **Runs in** | Sandboxed iframe | Main page context (no sandbox) |
| **Persistence** | Persistent — saved in chat history | Ephemeral — gone on reload/navigate |
| **Page access** | Isolated from parent by default | Full (DOM, cookies, localStorage) |
| **Forms** | Requires `allowForms` setting enabled | Always works (no sandbox) |
| **Best for** | Persistent visual content, dashboards, charts | Transient interactions, side effects, downloads, DOM manipulation |

Use Rich UI embeds for persistent visual content you want to stay in the conversation. Use `execute` for transient interactions like custom dialogs, triggering downloads, or reading page state.

## Use Cases

Rich UI embedding is perfect for:

- **Interactive dashboards** — Real-time data visualization and controls
- **Charts and graphs** — Interactive plotting with libraries like Plotly, D3.js, or Chart.js
- **Form interfaces** — Complex input forms with validation and dynamic behavior
- **Media players** — Video, audio, or interactive media content
- **Download triggers** — Especially useful for iOS PWA where native download links are blocked
- **Custom widgets** — Specialized UI components for specific tool functionality
- **External integrations** — Embedding content from external services or APIs
- **Human-triggered visualizations** — Actions that display results when a user clicks a button, e.g. generating a report or triggering a download

## Full Sample Action

<details>
<summary>Complete working Sample Action with Rich UI embed</summary>

This Action returns a styled card with stats, including the recommended height-reporting script:

```python
"""
title: Rich UI Demo Action
author: open-webui
version: 0.1.0
description: Demonstrates Rich UI embedding from an Action function.
"""

from pydantic import BaseModel, Field


class Action:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()

    async def action(self, body: dict, __user__=None, __event_emitter__=None) -> None:
        from fastapi.responses import HTMLResponse

        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; }
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    padding: 24px;
                    color: #fff;
                }
                .card {
                    background: rgba(255,255,255,0.15);
                    backdrop-filter: blur(10px);
                    border-radius: 16px;
                    padding: 24px;
                    border: 1px solid rgba(255,255,255,0.2);
                }
                h1 { font-size: 1.4em; margin-bottom: 8px; }
                p { opacity: 0.9; line-height: 1.5; margin-bottom: 12px; }
                .badge {
                    display: inline-block;
                    background: rgba(255,255,255,0.25);
                    padding: 4px 12px;
                    border-radius: 20px;
                    font-size: 0.85em;
                    font-weight: 600;
                }
                .stats {
                    display: flex;
                    gap: 16px;
                    margin-top: 16px;
                }
                .stat {
                    flex: 1;
                    text-align: center;
                    background: rgba(255,255,255,0.1);
                    border-radius: 12px;
                    padding: 12px;
                }
                .stat-value { font-size: 1.8em; font-weight: 700; }
                .stat-label { font-size: 0.8em; opacity: 0.8; margin-top: 4px; }
            </style>
        </head>
        <body>
            <div class="card">
                <h1>Rich UI Embed Demo</h1>
                <p>This embed renders <strong>below</strong> the message text.</p>
                <span class="badge">Action Embed</span>
                <div class="stats">
                    <div class="stat">
                        <div class="stat-value">42</div>
                        <div class="stat-label">Answers</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">99%</div>
                        <div class="stat-label">Accuracy</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">0ms</div>
                        <div class="stat-label">Latency</div>
                    </div>
                </div>
            </div>
            <script>
                // Report height to parent so the iframe auto-sizes
                function reportHeight() {
                    const h = document.documentElement.scrollHeight;
                    parent.postMessage({ type: 'iframe:height', height: h }, '*');
                }
                window.addEventListener('load', reportHeight);
                new ResizeObserver(reportHeight).observe(document.body);
            </script>
        </body>
        </html>
        """

        return HTMLResponse(content=html, headers={"Content-Disposition": "inline"})
```

</details>

## External Tool Example

For external tools served via HTTP endpoints:

```python
@app.post("/tools/dashboard")
async def create_dashboard():
    html = """
    <div style="padding: 20px;">
        <h2>System Dashboard</h2>
        <canvas id="myChart" width="400" height="200"></canvas>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <script>
            const ctx = document.getElementById('myChart').getContext('2d');
            new Chart(ctx, {
                type: 'line',
                data: { /* your chart data */ }
            });
        </script>
    </div>
    """

    return HTMLResponse(
        content=html,
        headers={"Content-Disposition": "inline"}
    )
```

The embedded content automatically inherits responsive design and integrates seamlessly with the chat interface, providing a native-feeling experience for users interacting with your tools.

## CORS and Direct Tools

Direct external tools are tools that run directly from the browser. In this case, the tool is called by JavaScript in the user's browser.
Because we depend on the Content-Disposition header, when using CORS on a remote tool server, the Open WebUI cannot read that header due to Access-Control-Expose-Headers, which prevents certain headers from being read from the fetch result.
To prevent this, you must set Access-Control-Expose-Headers to Content-Disposition. Check the example below of a tool using Node.js:


```javascript
const app = express();
const cors = require('cors');

app.use(cors())

app.get('/tools/dashboard', (req,res) => {
    let html = `
        <div style="padding: 20px;">
            <h2>System Dashboard</h2>
            <canvas id="myChart" width="400" height="200"></canvas>
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <script>
                const ctx = document.getElementById('myChart').getContext('2d');
                new Chart(ctx, {
                    type: 'line',
                    data: { /* your chart data */ }
                });
            </script>
        </div>
    `
    res.set({
        'Content-Disposition': 'inline'
        ,'Access-Control-Expose-Headers':'Content-Disposition'
    })
    res.send(html)
})
```

More info about the header: https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Access-Control-Expose-Headers

---
sidebar_position: 999
title: "Reserved Arguments"
---

:::warning

This tutorial is a community contribution and is not supported by the Open WebUI team. It serves only as a demonstration on how to customize Open WebUI for your specific use case. Want to contribute? Check out the contributing tutorial.

:::

# 🪄 Special Arguments

When developping your own `Tools`, `Functions` (`Filters`, `Pipes` or `Actions`), `Pipelines` etc, you can use special arguments explore the full spectrum of what Open-WebUI has to offer.

This page aims to detail the type and structure of each special argument as well as provide an example.

### `body`

A `dict` usually destined to go almost directly to the model. Although it is not strictly a special argument, it is included here for easier reference and because it contains itself some special arguments.

<details>
<summary>Example</summary>

```json

{
  "stream": true,
  "model": "my-cool-model",
  # lowercase string with - separated words: this is the ID of the model
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "What is in this picture?"
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAdYAAAGcCAYAAABk2YF[REDACTED]"
            # Images are passed as base64 encoded data
          }
        }
      ]
    },
    {
      "role": "assistant",
      "content": "The image appears to be [REDACTED]"
    },
  ],
  "features": {
    "image_generation": false,
    "code_interpreter": false,
    "web_search": false
  },
  "stream_options": {
    "include_usage": true
  },
  "metadata": "[The exact same dict as __metadata__]",
  "files": "[The exact same list as __files__]"
}

```

</details>

### `__user__`

A `dict` with user information.

Note that if the `UserValves` class is defined, its instance has to be accessed via `__user__["valves"]`. Otherwise, the `valves` keyvalue is missing entirely from `__user__`.

<details>
<summary>Example</summary>

```json
{
  "id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "email": "cheesy_dude@openwebui.com",
  "name": "Patrick",
  "role": "user",
  # role can be either `user` or `admin`
  "valves": "[the UserValve instance]"
}
```

</details>

### `__metadata__`

A `dict` with wide ranging information about the chat, model, files, etc.

<details>
<summary>Example</summary>

```json
{
  "user_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "chat_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "message_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "session_id": "xxxxxxxxxxxxxxxxxxxx",
  "tool_ids": null,
  # tool_ids is a list of str.
  "tool_servers": [],
  "files": "[Same as in body['files']]",
  # If no files are given, the files key exists in __metadata__ and its value is []
  "features": {
    "image_generation": false,
    "code_interpreter": false,
    "web_search": false
  },
  "variables": {
    "{{USER_NAME}}": "cheesy_username",
    "{{USER_LOCATION}}": "Unknown",
    "{{CURRENT_DATETIME}}": "2025-02-02 XX:XX:XX",
    "{{CURRENT_DATE}}": "2025-02-02",
    "{{CURRENT_TIME}}": "XX:XX:XX",
    "{{CURRENT_WEEKDAY}}": "Monday",
    "{{CURRENT_TIMEZONE}}": "Europe/Berlin",
    "{{USER_LANGUAGE}}": "en-US"
  },
  "model": "[The exact same dict as __model__]",
  "direct": false,
  "function_calling": "native",
  "type": "user_response",
  "interface": "open-webui"
}

```

</details>

:::tip Detecting Request Source

The `interface` field indicates where the request originated:
- **`"open-webui"`** - Request came from the web interface
- **Other/missing** - Request likely came from a direct API call

For direct API calls, some fields like `chat_id`, `message_id`, and `session_id` may be absent or `null` if not explicitly provided by the API client. You can use this to distinguish between WebUI and API requests in your filters:

```python
def inlet(self, body: dict, __metadata__: dict = None) -> dict:
    if __metadata__ and __metadata__.get("interface") == "open-webui":
        # Request from WebUI
        pass
    else:
        # Direct API request
        pass
    return body
```

:::

### `__model__`

A `dict` with information about the model.

<details>
<summary>Example</summary>

```json
{
  "id": "my-cool-model",
  "name": "My Cool Model",
  "object": "model",
  "created": 1746000000,
  "owned_by": "openai",
  # either openai or ollama
  "info": {
      "id": "my-cool-model",
      "user_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
      "base_model_id": "gpt-4o",
      # this is the name of model that the model endpoint serves
      "name": "My Cool Model",
      "params": {
      "system": "You are my best assistant. You answer [REDACTED]",
      "function_calling": "native"
      # custom options appear here, for example "Top K"
      },
      "meta": {
      "profile_image_url": "/static/favicon.png",
      "description": "Description of my-cool-model",
      "capabilities": {
          "vision": true,
          "usage": true,
          "citations": true
      },
      "position": 17,
      "tags": [
          {
          "name": "for_friends"
          },
          {
          "name": "vision_enabled"
          }
      ],
      "suggestion_prompts": null
      },
      "access_control": {
      "read": {
          "group_ids": [],
          "user_ids": []
      },
      "write": {
          "group_ids": [],
          "user_ids": []
      }
      },
      "is_active": true,
      "updated_at": 1740000000,
      "created_at": 1740000000
  },
  "preset": true,
  "actions": [],
  "tags": [
      {
          "name": "for_friends"
      },
      {
          "name": "vision_enabled"
      }
  ]
}

```

</details>

### `__messages__`

A `list` of the previous messages.

See the `body["messages"]` value above.

### `__chat_id__`

The `str` of the `chat_id`, representing the unique identifier of the current chat/conversation.

This parameter is reliably passed for all function invocations that originate from a chat context, including:
- Regular user messages
- Internal task calls (title generation, query generation, tag generation, etc.)

This allows stateful functions/pipes/manifolds to maintain per-chat state without fragmentation.

See also `__metadata__["chat_id"]` for accessing the same value via the metadata dict.

### `__session_id__`

The `str` of the `session_id`.

See the `__metadata__["session_id"]` value above.

### `__message_id__`

The `str` of the `message_id`.

See the `__metadata__["message_id"]` value above.

### `__event_emitter__`

A `Callable` used to display event information to the user.

### `__event_call__`

A `Callable` used for `Actions`.

### `__files__`

A `list` of files sent via the chat. Note that images are not considered files and are sent directly to the model as part of the `body["messages"]` list.

The actual binary of the file is not part of the arguments for performance reason, but the file remain nonetheless accessible by its path if needed. For example using `docker` the python syntax for the path could be:

```python
from pathlib import Path

the_file = Path(f"/app/backend/data/uploads/{__files__[0]["files"]["id"]}_{__files__[0]["files"]["filename"]}")
assert the_file.exists()
```

Note that the same files dict can also be accessed via `__metadata__["files"]` (and its value is `[]` if no files are sent) or via `body["files"]` (but the `files` key is missing entirely from `body` if no files are sent).

<details>
<summary>Example</summary>

```json

[
  {
    "type": "file",
    "file": {
      "id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
      "filename": "Napoleon - Wikipedia.pdf",
      "user_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
      "hash": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
      "data": {
        "content": "Napoleon - Wikipedia\n\n\nNapoleon I\n\nThe Emperor Napoleon in His Study at the\nTuileries, 1812\n\nEmperor of the French\n\n1st reign 18 May 1804 – 6 April 1814\n\nSuccessor Louis XVIII[a]\n\n2nd reign 20 March 1815 – 22 June 1815\n\nSuccessor Louis XVIII[a]\n\nFirst Consul of the French Republic\n\nIn office\n13 December 1799 – 18 May 1804\n\nBorn Napoleone Buonaparte\n15 August 1769\nAjaccio, Corsica, Kingdom of\nFrance\n\nDied 5 May 1821 (aged 51)\nLongwood, Saint Helena\n\nBurial 15 December 1840\nLes Invalides, Paris\n\nNapoleon\nNapoleon Bonaparte[b] (born Napoleone\nBuonaparte;[1][c] 15 August 1769 – 5 May 1821), later\nknown [REDACTED]",
        # The content value is the output of the document parser, the above example is with Tika as a document parser
      },
      "meta": {
        "name": "Napoleon - Wikipedia.pdf",
        "content_type": "application/pdf",
        "size": 10486578,
        # in bytes, here about 10Mb
        "data": {},
        "collection_name": "file-96xxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
        # always begins by 'file'
      },
      "created_at": 1740000000,
      "updated_at": 1740000000
    },
    "id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "url": "/api/v1/files/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "name": "Napoleon - Wikipedia.pdf",
    "collection_name": "file-96xxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    "status": "uploaded",
    "size": 10486578,
    "error": "",
    "itemId": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    # itemId is not the same as file["id"]
  }
]

```

</details>

### `__request__`

An instance of `fastapi.Request`. You can read more in the [migration page](/features/extensibility/plugin/migration/) or in [fastapi's documentation](https://fastapi.tiangolo.com/reference/request/).

### `__task__`

A `str` for the type of task. Its value is just a shorthand for `__metadata__["task"]` if present, otherwise `None`.

<details>
<summary>Possible values</summary>

```json

[
    "title_generation",
    "tags_generation",
    "emoji_generation",
    "query_generation",
    "image_prompt_generation",
    "autocomplete_generation",
    "function_calling",
    "moa_response_generation"
]
```

</details>

### `__task_body__`

A `dict` containing the `body` needed to accomplish a given `__task__`. Its value is just a shorthand for `__metadata__["task_body"]` if present, otherwise `None`.

Its structure is the same as `body` above, with modifications like using the appropriate model and system message etc.

### `__tools__`

A `list` of `ToolUserModel` instances.

For details the attributes of `ToolUserModel` instances, the code can be found in [tools.py](https://github.com/open-webui/open-webui/blob/main/backend/open_webui/models/tools.py).

---
sidebar_position: 1
title: "Functions"
---

## 🚀 What Are Functions?

Functions are like **plugins** for Open WebUI. They help you **extend its capabilities**—whether it’s adding support for new AI model providers like Anthropic or Vertex AI, tweaking how messages are processed, or introducing custom buttons to the interface for better usability.

Unlike external tools that may require complex integrations, **Functions are built-in and run within the Open WebUI environment.** That means they are fast, modular, and don’t rely on external dependencies.

Think of Functions as **modular building blocks** that let you enhance how the WebUI works, tailored exactly to what you need. They’re lightweight, highly customizable, and written in **pure Python**, so you have the freedom to create anything—from new AI-powered workflows to integrations with anything you use, like Google Search or Home Assistant.

:::danger ⚠️ Security Warning

**Functions execute arbitrary Python code on your server.** Only install Functions from trusted sources. Before importing any Function, review its source code to understand what it does. A malicious Function could access your file system, exfiltrate data, or compromise your system. See the [Security Policy](/security) for more details.

:::

---

## 🏗️ Types of Functions

There are **three types of Functions** in Open WebUI, each with a specific purpose. Let’s break them down and explain exactly what they do:

---

### 1. [**Pipe Function** – Create Custom "Agents/Models"](./pipe.mdx)

A **Pipe Function** is how you create **custom agents/models** or integrations, which then appear in the interface as if they were standalone models.

**What does it do?**
- Pipes let you define complex workflows. For instance, you could create a Pipe that sends data to **Model A** and **Model B**, processes their outputs, and combines the results into one finalized answer.
- Pipes don’t even have to use AI! They can be setups for **search APIs**, **weather data**, or even systems like **Home Assistant**. Basically, anything you’d like to interact with can become part of Open WebUI.

**Use case example:**
Imagine you want to query Google Search directly from Open WebUI. You can create a Pipe Function that:
1. Takes your message as the search query.
2. Sends the query to Google Search’s API.
3. Processes the response and returns it to you inside the WebUI like a normal "model" response.

When enabled, **Pipe Functions show up as their own selectable model**. Use Pipes whenever you need custom functionality that works like a model in the interface.

For a detailed guide, see [**Pipe Functions**](./pipe.mdx).

---

### 2. [**Filter Function** – Modify Inputs and Outputs](./filter.mdx)

A **Filter Function** is like a tool for tweaking data before it gets sent to the AI **or** after it comes back.

**What does it do?**
Filters act as "hooks" in the workflow and have two main parts:
- **Inlet**: Adjust the input that is sent to the model. For example, adding additional instructions, keywords, or formatting tweaks.
- **Outlet**: Modify the output that you receive from the model. For instance, cleaning up the response, adjusting tone, or formatting data into a specific style.

**Use case example:**
Suppose you’re working on a project that needs precise formatting. You can use a Filter to ensure:
1. Your input is always transformed into the required format.
2. The output from the model is cleaned up before being displayed.

Filters are **linked to specific models** or can be enabled for all models **globally**, depending on your needs.

Check out the full guide for more examples and instructions: [**Filter Functions**](./filter.mdx).

---

### 3. [**Action Function** – Add Custom Buttons](./action.mdx)

An **Action Function** is used to add **custom buttons** to the chat interface.

**What does it do?**
Actions allow you to define **interactive shortcuts** that trigger specific functionality directly from the chat. These buttons appear underneath individual chat messages, giving you convenient, one-click access to the actions you define.

**Use case example:**
Let’s say you often need to summarize long messages or generate specific outputs like translations. You can create an Action Function to:
1. Add a “Summarize” button under every incoming message.
2. When clicked, it triggers your custom function to process that message and return the summary.

Buttons provide a **clean and user-friendly way** to interact with extended functionality you define.

Learn how to set them up in the [**Action Functions Guide**](./action.mdx).

---

## 🛠️ How to Use Functions

Here's how to put Functions to work in Open WebUI:

### 1. **Install Functions**
You can install Functions via the Open WebUI interface or by importing them manually. You can find community-created functions on the [Open WebUI Community Site](https://openwebui.com/search).

⚠️ **Be cautious.** Only install Functions from trusted sources. Running unknown code poses security risks.

---

### 2. **Enable Functions**
Functions must be explicitly enabled after installation:
- When you enable a **Pipe Function**, it becomes available as its own **model** in the interface.
- For **Filter** and **Action Functions**, enabling them isn’t enough—you also need to assign them to specific models or enable them globally for all models.

---

### 3. **Assign Filters or Actions to Models**
- Navigate to `Workspace => Models` and assign your Filter or Action to the relevant model there.
- Alternatively, enable Functions for **all models globally** by going to `Workspace => Functions`, selecting the "..." menu, and toggling the **Global** switch.

---

### Quick Summary
- **Pipes** appear as standalone models you can interact with.
- **Filters** modify inputs/outputs for smoother AI interactions.
- **Actions** add clickable buttons to individual chat messages.

Once you’ve followed the setup process, Functions will seamlessly enhance your workflows.

---

## ✅ Why Use Functions?

Functions are designed for anyone who wants to **unlock new possibilities** with Open WebUI:

- **Extend**: Add new models or integrate with non-AI tools like APIs, databases, or smart devices.
- **Optimize**: Tweak inputs and outputs to fit your use case perfectly.
- **Simplify**: Add buttons or shortcuts to make the interface intuitive and efficient.

Whether you’re customizing workflows for specific projects, integrating external data, or just making Open WebUI easier to use, Functions are the key to taking control of your instance.

---

### 📝 Final Notes:
1. Always install Functions from **trusted sources only**.
2. Make sure you understand the difference between Pipe, Filter, and Action Functions to use them effectively.
3. Explore the official guides:
   - [Pipe Functions Guide](./pipe.mdx)
   - [Filter Functions Guide](./filter.mdx)
   - [Action Functions Guide](./action.mdx)

By leveraging Functions, you’ll bring entirely new capabilities to your Open WebUI setup. Start experimenting today! 🚀

---
sidebar_position: 2
title: "Action Function"
---

Action functions allow you to write custom buttons that appear in the message toolbar for end users to interact with. This feature enables more interactive messaging, allowing users to grant permission before a task is performed, generate visualizations of structured data, download an audio snippet of chats, and many other use cases.

:::warning Use Async Functions for Future Compatibility
Action functions should always be defined as `async`. The backend is progressively moving toward fully async execution, and synchronous functions may block execution or cause issues in future releases.
:::

Actions are admin-managed functions that extend the chat interface with custom interactive capabilities. When a message is generated by a model that has actions configured, these actions appear as clickable buttons beneath the message.

A scaffold of Action code can be found [in the community section](https://openwebui.com/f/hub/custom_action/). For more Action Function examples built by the community, visit [https://openwebui.com/search](https://openwebui.com/search).

An example of a graph visualization Action can be seen in the video below.

<div align="center">
	<a href="#">
		<img
			src="/images/pipelines/graph-viz-action.gif"
			alt="Graph Visualization Action"
		/>
	</a>
</div>

## Action Function Architecture

Actions are Python-based functions that integrate directly into the chat message toolbar. They execute server-side and can interact with users through real-time events, modify message content, and access the full Open WebUI context.

### Function Structure

Actions follow a specific class structure with an `action` method as the main entry point:

```python
class Action:
    def __init__(self):
        self.valves = self.Valves()

    class Valves(BaseModel):
        # Configuration parameters
        parameter_name: str = "default_value"
        priority: int = 0  # Controls button display order (lower = appears first)

    async def action(self, body: dict, __user__=None, __event_emitter__=None, __event_call__=None):
        # Action implementation
        return {"content": "Modified message content"}
```

### Action Method Parameters

The `action` method receives several parameters that provide access to the execution context:

- **`body`** - Dictionary containing the message data and context
- **`__user__`** - Current user object with permissions and settings
- **`__event_emitter__`** - Function to send real-time updates to the frontend
- **`__event_call__`** - Function for bidirectional communication (confirmations, inputs)
- **`__model__`** - Model information that triggered the action
- **`__request__`** - FastAPI request object for accessing headers, etc.
- **`__id__`** - Action ID (useful for multi-action functions)

## Event System Integration

Actions can utilize Open WebUI's real-time event system for interactive experiences:

### Event Emitter (`__event_emitter__`)

**For more information about Events and Event emitters, see [Events and Event Emitters](https://docs.openwebui.com/features/extensibility/plugin/events/).**

Send real-time updates to the frontend during action execution:

```python
async def action(self, body: dict, __event_emitter__=None):
    # Send status updates
    await __event_emitter__({
        "type": "status",
        "data": {"description": "Processing request..."}
    })

    # Send notifications
    await __event_emitter__({
        "type": "notification",
        "data": {"type": "info", "content": "Action completed successfully"}
    })
```

### Event Call (`__event_call__`)
Request user input or confirmation during execution:

```python
async def action(self, body: dict, __event_call__=None):
    # Request user confirmation
    response = await __event_call__({
        "type": "confirmation",
        "data": {
            "title": "Confirm Action",
            "message": "Are you sure you want to proceed?"
        }
    })

    # Request user input
    user_input = await __event_call__({
        "type": "input",
        "data": {
            "title": "Enter Value",
            "message": "Please provide additional information:",
            "placeholder": "Type your input here..."
        }
    })
```

## Action Types and Configurations

### Single Actions
Standard actions with one `action` method:

```python
async def action(self, body: dict, **kwargs):
    # Single action implementation
    return {"content": "Action result"}
```

### Multi-Actions
Functions can define multiple sub-actions through an `actions` array:

```python
actions = [
    {
        "id": "summarize",
        "name": "Summarize",
        "icon_url": "https://example.com/icons/summarize.svg"
    },
    {
        "id": "translate",
        "name": "Translate",
        "icon_url": "https://example.com/icons/translate.svg"
    }
]

async def action(self, body: dict, __id__=None, **kwargs):
    if __id__ == "summarize":
        # Summarization logic
        return {"content": "Summary: ..."}
    elif __id__ == "translate":
        # Translation logic
        return {"content": "Translation: ..."}
```

### Global vs Model-Specific Actions
- **Global Actions** - Turn on the toggle in the Action's settings, to globally enable it for all users and all models.
- **Model-Specific Actions** - Configure enabled actions for specific models in the model settings.

### Button Display Order (Priority)

Action buttons beneath assistant messages are sorted by their `priority` valve value in **ascending order** — lower values appear first (leftmost), higher values appear later (rightmost). The default priority is `0`.

To control the order, add a `priority` field to your Action's Valves:

```python
class Valves(BaseModel):
    priority: int = 0  # Lower = appears first in the button row
```

This uses the same priority mechanism as [filter functions](/features/extensibility/plugin/functions/filter), so the behavior is consistent across the plugin system. Without a `priority` valve, actions default to `0` and their order among equal priorities is determined **alphabetically by function ID**.

## Advanced Capabilities

### Background Task Execution
For long-running operations, actions can integrate with the task system:

```python
async def action(self, body: dict, __event_emitter__=None):
    # Start long-running process
    await __event_emitter__({
        "type": "status",
        "data": {"description": "Starting background processing..."}
    })

    # Perform time-consuming operation
    result = await some_long_running_function()

    return {"content": f"Processing completed: {result}"}
```

### File and Media Handling
Actions can work with uploaded files and generate new media:

```python
async def action(self, body: dict):
    message = body

    # Access uploaded files
    if message.get("files"):
        for file in message["files"]:
            # Process file based on type
            if file["type"] == "image":
                # Image processing logic
                pass

    # Return new files
    return {
        "content": "Analysis complete",
        "files": [
            {
                "type": "image",
                "url": "generated_chart.png",
                "name": "Analysis Chart"
            }
        ]
    }
```

### User Context and Permissions
Actions can access user information and respect permissions:

```python
async def action(self, body: dict, __user__=None):
    if __user__["role"] != "admin":
        return {"content": "This action requires admin privileges"}

    user_name = __user__["name"]
    return {"content": f"Hello {user_name}, admin action completed"}
```

## Example - Specifying Action Frontmatter

Each Action function can include a docstring at the top to define metadata for the button. This helps customize the display and behavior of your Action in Open WebUI.

Example of supported frontmatter fields:
- `title`: Display name of the Action.
- `author`: Name of the creator.
- `version`: Version number of the Action.
- `required_open_webui_version`: Minimum compatible version of Open WebUI.
- `icon_url (optional)`: A URL pointing to an icon image (PNG, SVG, JPEG, etc.). While base64 data URIs are technically supported, **using a hosted URL is strongly recommended** — see the warning below.

:::danger Avoid Base64 Icons — Use URLs Instead
Do **not** embed base64-encoded images as your `icon_url`. The icon data for every action is included in the `/api/models` API response, which is sent to the frontend on every page load for **every model** that has the action enabled.

**Example of the impact:** If you use a 500 KB base64 icon for an action, and that action is enabled on 20 models, the API response grows by **20 × 500 KB = ~10 MB** — just for that one action. If you have three such actions, that becomes **~30 MB of unnecessary payload**. This will:
- **Significantly slow down frontend load times** for all users
- **Increase backend memory usage and network bandwidth** on every request
- **Degrade the overall user experience**, especially on slower connections

Instead, host your icon as a static file (e.g., on your web server, a CDN, or a public URL) and reference it by URL. This keeps the API payload minimal.
:::

**Example (Recommended — URL icon):**

<details>
<summary>Example</summary>

```python
"""
title: Enhanced Message Processor
author: @admin
version: 1.2.0
required_open_webui_version: 0.5.0
icon_url: https://example.com/icons/message-processor.svg
requirements: requests,beautifulsoup4
"""

from pydantic import BaseModel

class Action:
    def __init__(self):
        self.valves = self.Valves()

    class Valves(BaseModel):
        api_key: str = ""
        processing_mode: str = "standard"

    async def action(
        self,
        body: dict,
        __user__=None,
        __event_emitter__=None,
        __event_call__=None,
    ):
        # Send initial status
        await __event_emitter__({
            "type": "status",
            "data": {"description": "Processing message..."}
        })

        # Get user confirmation
        response = await __event_call__({
            "type": "confirmation",
            "data": {
                "title": "Process Message",
                "message": "Do you want to enhance this message?"
            }
        })

        if not response:
            return {"content": "Action cancelled by user"}

        # Process the message
        original_content = body.get("content", "")
        enhanced_content = f"Enhanced: {original_content}"

        return {"content": enhanced_content}
```

</details>

## Best Practices

### Error Handling
Always implement proper error handling in your actions:

```python
async def action(self, body: dict, __event_emitter__=None):
    try:
        # Action logic here
        result = perform_operation()
        return {"content": f"Success: {result}"}
    except Exception as e:
        await __event_emitter__({
            "type": "notification",
            "data": {"type": "error", "content": f"Action failed: {str(e)}"}
        })
        return {"content": "Action encountered an error"}
```

### Performance Considerations
- Use async/await for I/O operations
- Implement timeouts for external API calls
- Provide progress updates for long-running operations
- Consider using background tasks for heavy processing

### User Experience
- Always provide clear feedback through event emitters
- Use confirmation dialogs for destructive actions
- Include helpful error messages

## Integration with Open WebUI Features

Actions integrate seamlessly with other Open WebUI features:
- **Models** - Actions can be model-specific or global
- **Tools** - Actions can invoke external tools and APIs
- **Files** - Actions can process uploaded files and generate new ones
- **Memory** - Actions can access conversation history and context
- **Permissions** - Actions respect user roles and access controls
- **[Rich UI Embedding](/features/extensibility/plugin/development/rich-ui)** - Actions can return HTML content that renders as interactive iframes in the chat
For more examples and community-contributed actions, visit [https://openwebui.com/search](https://openwebui.com/search) where you can discover, download, and explore custom functions built by the Open WebUI community.

---
sidebar_position: 3
title: "Filter Function"
---

# 🪄 Filter Function: Modify Inputs and Outputs

Welcome to the comprehensive guide on Filter Functions in Open WebUI! Filters are a flexible and powerful **plugin system** for modifying data *before it's sent to the Large Language Model (LLM)* (input) or *after it’s returned from the LLM* (output). Whether you’re transforming inputs for better context or cleaning up outputs for improved readability, **Filter Functions** let you do it all.

This guide will break down **what Filters are**, how they work, their structure, and everything you need to know to build powerful and user-friendly filters of your own. Let’s dig in, and don’t worry—I’ll use metaphors, examples, and tips to make everything crystal clear! 🌟

---

## 🌊 What Are Filters in Open WebUI?

Imagine Open WebUI as a **stream of water** flowing through pipes:

- **User inputs** and **LLM outputs** are the water.
- **Filters** are the **water treatment stages** that clean, modify, and adapt the water before it reaches the final destination.

Filters sit in the middle of the flow—like checkpoints—where you decide what needs to be adjusted.

Here’s a quick summary of what Filters do:

1. **Modify User Inputs (Inlet Function)**: Tweak the input data before it reaches the AI model. This is where you enhance clarity, add context, sanitize text, or reformat messages to match specific requirements.
2. **Intercept Model Outputs (Stream Function)**: Capture and adjust the AI’s responses **as they’re generated** by the model. This is useful for real-time modifications, like filtering out sensitive information or formatting the output for better readability.
3. **Modify Model Outputs (Outlet Function)**: Adjust the AI's response **after it’s processed**, before showing it to the user. This can help refine, log, or adapt the data for a cleaner user experience.

> **Key Concept:** Filters are not standalone models but tools that enhance or transform the data traveling *to* and *from* models.

Filters are like **translators or editors** in the AI workflow: you can intercept and change the conversation without interrupting the flow.

---

## 🗺️ Structure of a Filter Function: The Skeleton

Let's start with the simplest representation of a Filter Function. Don't worry if some parts feel technical at first—we’ll break it all down step by step!

### 🦴 Basic Skeleton of a Filter

```python
from pydantic import BaseModel
from typing import Optional

class Filter:
    # Valves: Configuration options for the filter
    class Valves(BaseModel):
        pass

    def __init__(self):
        # Initialize valves (optional configuration for the Filter)
        self.valves = self.Valves()

    def inlet(self, body: dict) -> dict:
        # This is where you manipulate user inputs.
        print(f"inlet called: {body}")
        return body

    def stream(self, event: dict) -> dict:
        # This is where you modify streamed chunks of model output.
        print(f"stream event: {event}")
        return event

    def outlet(self, body: dict) -> None:
        # This is where you manipulate model outputs.
        print(f"outlet called: {body}")
```

---

### 🆕 🧲 Toggle Filter Example: Adding Interactivity and Icons (New in Open WebUI 0.6.10)

Filters can do more than simply modify text—they can expose UI toggles and display custom icons. For instance, you might want a filter that can be turned on/off with a user interface button, and displays a special icon in Open WebUI’s message input UI.

Here’s how you could create such a toggle filter:

```python
from pydantic import BaseModel, Field
from typing import Optional

class Filter:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()
        self.toggle = True # IMPORTANT: This creates a switch UI in Open WebUI
        # TIP: Use a hosted URL for your icon instead of base64 to avoid API payload bloat.
        # See the Action Function docs for details on why base64 icons are not recommended.
        self.icon = "https://example.com/icons/lightbulb.svg"
        pass

    async def inlet(
        self, body: dict, __event_emitter__, __user__: Optional[dict] = None
    ) -> dict:
        await __event_emitter__(
            {
                "type": "status",
                "data": {
                    "description": "Toggled!",
                    "done": True,
                    "hidden": False,
                },
            }
        )
        return body
```

#### 🖼️ What’s happening?
-   **toggle = True** creates a switch UI in Open WebUI—users can manually enable or disable the filter in real time.
-   **icon** will show up as a little image next to the filter’s name. You can use a URL pointing to any image (SVG, PNG, JPEG, etc.). While base64 data URIs are technically supported, **using a hosted URL is strongly recommended** to avoid bloating the `/api/models` payload — see the [Action Function icon_url warning](/features/extensibility/plugin/functions/action#example---specifying-action-frontmatter) for details.
-   **The `inlet` function** uses the `__event_emitter__` special argument to broadcast feedback/status to the UI, such as a little toast/notification that reads "Toggled!"

![Toggle Filter](/images/features/plugin/functions/toggle-filter.png)

You can use these mechanisms to make your filters dynamic, interactive, and visually unique within Open WebUI’s plugin ecosystem.

---

## ⚙️ Filter Administration & Configuration

### 🌐 Global Filters vs. Model-Specific Filters

Open WebUI provides a flexible multi-level filter system that allows you to control which filters are active, how they're enabled, and who can toggle them. Understanding this system is crucial for effective filter management.

#### Filter Activation States

Filters can exist in one of four states, controlled by two boolean flags in the database:

| State | `is_active` | `is_global` | Effect |
|-------|------------|------------|--------|
| **Globally Enabled** | ✅ `True` | ✅ `True` | Applied to **ALL** models automatically, cannot be disabled per-model |
| **Globally Disabled** | ❌ `False` | `True` | Not applied anywhere - even though the filter is globally enabled, the filter itself is disabled |
| **Model-Specific** | ✅ `True` | ❌ `False` | Only applied to models where the admin explicitly enables it |
| **Inactive** | ❌ `False` | `False` | Not applied anywhere, even if filter is enabled for a model by the admin - the filter itself is turned off |

:::tip Global Filter Behavior
When a filter is set as **Global** (`is_global=True`) and **Active** (`is_active=True`), it becomes **force-enabled** for all models:
- It appears in every model's filter list as **checked and greyed out**
- Admins **cannot** uncheck it in model settings
- It runs on **every** chat completion request, regardless of model
:::

#### Admin Panel: Making a Filter Global

**Location:** Admin Panel → Functions → Filter Management

To make a filter global:
1. Navigate to the Admin Panel
2. Click on **Functions** in the sidebar
3. Find your filter in the list
4. Click the **three-dot menu (⋮)** next to the filter
5. Click the **🌐 Globe icon** to toggle `is_global`
6. Ensure the filter is also **Active** (green toggle switch)

**API Endpoint:**
```http
POST /functions/id/{filter_id}/toggle/global
```

**Visual Indicators:**
- 🟢 Green toggle = `is_active=True` (filter is active)
- 🌐 Highlighted globe icon = `is_global=True` (applies to all models)

---

### 🎛️ The Two-Tier Filter System

Open WebUI uses a sophisticated two-tier system for managing filters on a per-model basis. This can be confusing at first, but it's designed to support both **always-on filters** and **user-toggleable filters**.

#### Tier 1: FiltersSelector (Which filters are available?)

**Location:** Model Settings → Filters → "Filters" Section

This controls which filters are **available** for a specific model.

**Behavior:**
- Shows **all** filters (both global and model-specific)
- **Global filters** appear as checked and disabled (can't be unchecked)
- **Regular filters** can be toggled on/off
- Saves to: `model.meta.filterIds` in the database

**Example:**
```json
{
  "meta": {
    "filterIds": ["filter-uuid-1", "filter-uuid-2"]
  }
}
```

#### Tier 2: DefaultFiltersSelector (Which toggleable filters start enabled?)

**Location:** Model Settings → Filters → "Default Filters" Section

This section **only appears** when at least one toggleable filter is selected (or is global).

**Purpose:** Controls which toggleable filters are **enabled by default** for new chats.

**What is a "Toggleable" Filter?**

A filter becomes toggleable when its Python code includes:
```python
class Filter:
    def __init__(self):
        self.toggle = True  # This makes it toggleable!
```

**Behavior:**
- Only shows filters with `toggle=True`
- Only shows filters that are either:
  - In `filterIds` (selected for this model), OR
  - Have `is_global=true` (globally enabled)
- Controls whether the filter is **ON** or **OFF** by default in the chat UI
- Saves to: `model.meta.defaultFilterIds`

**Example:**
```json
{
  "meta": {
    "filterIds": ["filter-uuid-1", "filter-uuid-2", "filter-uuid-3"],
    "defaultFilterIds": ["filter-uuid-2"]
  }
}
```

**Interpretation:**
- All three filters are available for this model
- Only `filter-uuid-2` starts enabled by default
- If `filter-uuid-1` and `filter-uuid-3` have `toggle=True`, users can enable them manually in the chat UI

---

### 🔄 Toggleable Filters vs. Always-On Filters

Understanding the difference between these two types is key to using the filter system effectively.

#### Always-On Filters (No `toggle` property)

**Characteristics:**
- Run automatically whenever the filter is active for a model
- **No user control** in the chat interface
- Do **not** appear in the "Default Filters" section
- Do **not** show up in the chat integrations menu (⚙️ icon)

**Use Cases:**
- **Content moderation** - Filter profanity, hate speech, or inappropriate content
- **PII scrubbing** - Automatically redact emails, phone numbers, SSNs, credit card numbers
- **Prompt injection detection** - Block attempts to manipulate the system prompt
- **Input/output logging** - Track all conversations for audit or analytics
- **Cost tracking** - Estimate and log token usage for billing
- **Rate limiting** - Enforce request limits per user or globally
- **Language enforcement** - Ensure responses are in a specific language
- **Company policy enforcement** - Inject legal disclaimers or compliance notices
- **Model routing** - Redirect requests to different models based on content

**Example:**
```python
class ContentModerationFilter:
    def __init__(self):
        # No toggle property - this is an always-on filter
        pass
    
    def inlet(self, body: dict) -> dict:
        # Always scrub PII before sending to model
        last_message = body["messages"][-1]["content"]
        body["messages"][-1]["content"] = self.scrub_pii(last_message)
        return body
```

#### Toggleable Filters (`toggle=True`)

**Characteristics:**
- Appear as **switches in the chat UI** (in the integrations menu - ⚙️ icon)
- Users can **enable/disable** them per chat session
- **Do** appear in the "Default Filters" section
- `defaultFilterIds` controls their initial state (ON or OFF)

**Use Cases:**
- **Web search integration** - User decides when to search the web for context
- **Citation mode** - User controls when to require sources in responses
- **Verbose/detailed mode** - User toggles between concise and detailed responses
- **Translation filters** - User enables translation to/from specific languages
- **Code formatting** - User chooses when to apply syntax highlighting or linting
- **Thinking/reasoning toggle** - Show or hide model's chain-of-thought reasoning
- **Markdown rendering** - Toggle between raw text and formatted output
- **Anonymization mode** - User enables when discussing sensitive topics
- **Expert mode** - Inject domain-specific context (legal, medical, technical)
- **Creative writing mode** - Adjust temperature and style for creative tasks

**Example:**
```python
class WebSearchFilter:
    def __init__(self):
        self.toggle = True  # User can turn on/off
        self.icon = "https://example.com/icons/web-search.svg"  # Shows in UI
    
    async def inlet(self, body: dict, __event_emitter__) -> dict:
        # Only runs when user has enabled this filter
        await __event_emitter__({
            "type": "status",
            "data": {"description": "Searching the web...", "done": False}
        })
        # ... perform web search ...
        return body
```

**Where Toggleable Filters Appear:**

1.  **Model Settings → Default Filters Section**
    -   Configure which filters start enabled
2.  **Chat UI → Integrations Menu (⚙️ icon)**
    -   Users can toggle filters on/off per chat
    -   Shows custom icons if provided
    -   Realtime enable/disable

---

### 📊 Filter Execution Flow

Here's the complete flow from admin configuration to filter execution:

**1. ADMIN PANEL (Filter Creation & Global Settings)**
- Admin Panel → Functions → Create New Function
- Set type="filter"
- Toggle is_active (enable/disable filter globally)
- Toggle is_global (apply to all models)

**2. MODEL CONFIGURATION (Per-Model Filter Selection)**
- Model Settings → Filters Section
- FiltersSelector: Select which filters for this model
- DefaultFiltersSelector: Set default enabled state (only for toggleable filters)

**3. CHAT UI (User Interaction - Toggleable Filters Only)**
- Chat → Integrations Menu (⚙️) → Toggle Filters
- Users can enable/disable toggleable filters
- Always-on filters run automatically (no UI control)

**4. REQUEST PROCESSING (Filter Compilation)**
- Backend: get_sorted_filter_ids()
- Fetch global filters (is_global=True, is_active=True)
- Add model-specific filters from model.meta.filterIds
- Filter by is_active status
- For toggleable filters: Check user's enabled state
- Sort by priority (from valves)

**5. FILTER EXECUTION**
- Execute inlet() filters (pre-request)
- Send modified request to LLM
- Execute stream() filters (during streaming)
- Execute outlet() filters (post-response)

---

### 📡 Filter Behavior with API Requests

When using Open WebUI's API endpoints directly (e.g., via `curl` or external applications), filters behave differently than when the request comes from the web interface. Understanding these differences is crucial for building effective filters.

#### Key Behavioral Differences

| Function | WebUI Request | Direct API Request |
|----------|--------------|-------------------|
| `inlet()` | ✅ Always called | ✅ Always called |
| `stream()` | ✅ Called during streaming | ✅ Called during streaming |
| `outlet()` | ✅ Called after response | ❌ **NOT called** by default |
| `__event_emitter__` | ✅ Shows UI feedback | ⚠️ Runs but no UI to display |

:::warning Outlet Not Called for API Requests
The `outlet()` function is **only triggered for WebUI chat requests**, not for direct API calls to `/api/chat/completions`. This is because `outlet()` is invoked by the WebUI's `/api/chat/completed` endpoint after the chat is finished.

If you need `outlet()` processing for API requests, your API client must call `/api/chat/completed` after receiving the full response.
:::

#### Triggering Outlet for API Requests

To invoke `outlet()` filters for API requests, your client must make a second request to `/api/chat/completed` after receiving the complete response:

```bash
# After receiving the full response from /api/chat/completions, call:
curl -X POST http://localhost:3000/api/chat/completed \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.1",
    "messages": [
      {"role": "user", "content": "Hello"},
      {"role": "assistant", "content": "Hi there! How can I help you?"}
    ],
    "chat_id": "optional-chat-id",
    "session_id": "optional-session-id"
  }'
```

:::tip
Include the full conversation in `messages`, including the assistant's response. The `chat_id` and `session_id` are optional but recommended for proper logging and state tracking.
:::

#### Detecting API vs WebUI Requests

You can detect whether a request originates from the WebUI or a direct API call by checking the `__metadata__` argument:

```python
def inlet(self, body: dict, __metadata__: dict = None) -> dict:
    # Check if request is from WebUI
    interface = __metadata__.get("interface") if __metadata__ else None
    
    if interface == "open-webui":
        print("Request from WebUI")
    else:
        print("Direct API request")
    
    # You can also check for presence of chat context
    chat_id = __metadata__.get("chat_id") if __metadata__ else None
    if not chat_id:
        print("No chat context - likely a direct API call")
    
    return body
```

#### Example: Rate Limiting for All Requests

Since `inlet()` is always called, use it for rate limiting that applies to both WebUI and API requests:

```python
from pydantic import BaseModel, Field
from typing import Optional
import time

class Filter:
    class Valves(BaseModel):
        requests_per_minute: int = Field(default=60, description="Max requests per minute per user")
    
    def __init__(self):
        self.valves = self.Valves()
        self.user_requests = {}  # Track requests per user
    
    def inlet(self, body: dict, __user__: dict = None) -> dict:
        if not __user__:
            return body
        
        user_id = __user__.get("id")
        current_time = time.time()
        
        # Clean old entries and count recent requests
        if user_id not in self.user_requests:
            self.user_requests[user_id] = []
        
        # Keep only requests from the last minute
        self.user_requests[user_id] = [
            t for t in self.user_requests[user_id] 
            if current_time - t < 60
        ]
        
        if len(self.user_requests[user_id]) >= self.valves.requests_per_minute:
            raise Exception(f"Rate limit exceeded: {self.valves.requests_per_minute} requests/minute")
        
        self.user_requests[user_id].append(current_time)
        return body
```

#### Example: Logging All API Usage

Track token usage and requests for both WebUI and direct API calls:

```python
from pydantic import BaseModel, Field
from typing import Optional
import logging

class Filter:
    class Valves(BaseModel):
        log_level: str = Field(default="INFO", description="Logging level")
    
    def __init__(self):
        self.valves = self.Valves()
        self.logger = logging.getLogger("api_usage")
    
    def inlet(self, body: dict, __user__: dict = None, __metadata__: dict = None) -> dict:
        user_email = __user__.get("email", "unknown") if __user__ else "anonymous"
        model = body.get("model", "unknown")
        interface = __metadata__.get("interface", "api") if __metadata__ else "api"
        chat_id = __metadata__.get("chat_id") if __metadata__ else None
        
        self.logger.info(
            f"Request: user={user_email}, model={model}, "
            f"interface={interface}, chat_id={chat_id or 'none'}"
        )
        
        return body
```

:::note Event Emitter Behavior
Filters that use `__event_emitter__` will still execute for API requests, but since there's no WebUI to display the events, the status messages won't be visible. The filter logic still runs—only the visual feedback is missing.
:::

---

### ⚡ Filter Priority & Execution Order

When multiple filters are active, they execute in a specific order determined by their **priority** value. Understanding this is crucial when building filter chains where one filter depends on another's changes.

#### Setting Filter Priority

Priority is configured via the `Valves` class using a `priority` field:

```python
class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=0,
            description="Filter execution order. Lower values run first."
        )
    
    def __init__(self):
        self.valves = self.Valves()
    
    def inlet(self, body: dict) -> dict:
        # This filter's execution order depends on its priority value
        return body
```

#### Priority Ordering Rules

| Priority Value | Execution Order |
|---------------|-----------------|
| `0` (default) | Runs first |
| `1` | Runs after priority 0 |
| `2` | Runs after priority 1 |

:::tip Lower Priority = Earlier Execution
Filters are sorted in **ascending** order by priority. A filter with `priority=0` runs **before** a filter with `priority=1`, which runs before `priority=2`, and so forth. When multiple filters share the same priority value, they are sorted **alphabetically by function ID** for deterministic ordering.
:::

---

### 🔗 Data Passing Between Filters

When multiple filters are active, each filter in the chain receives the **modified data from the previous filter**. The returned value from one filter becomes the input to the next filter in the priority order.

```
User Input
    ↓
Model Router Filter (priority=0) → changes parts of the body
    ↓
Context Manager Filter (priority=1) → receives modified body ✓
    ↓
Logging Filter (priority=2) → receives body with all previous changes ✓
    ↓
LLM Request (sends final modified body to OpenAI/Ollama API)
```

:::warning Important: Always Return the Body
If your filter modifies the `body`, you **must** return it. The returned value is passed to the next filter. If you return `None`, subsequent filters will fail.

```python
async def inlet(self, body: dict, __event_emitter__) -> dict:
    body["messages"].append({"role": "system", "content": "Hello"})
    return body  # Don't forget this!
```
:::

---

### 🔌 Injecting Extra API Body Parameters

Inlet filters can inject **extra fields into the request body** that get forwarded to the external LLM API. This is useful for API-specific parameters that Open WebUI doesn't expose in the UI.

The request body flows from your inlet filter to the LLM API without stripping unknown fields — only internal keys like `metadata`, `features`, `tool_ids`, `files`, and `skill_ids` are removed. Any other field you add will be serialized to JSON and sent to the API provider.

#### Example: OpenAI Safety Identifier

OpenAI recommends sending a `safety_identifier` with each request for abuse detection. You can inject this automatically via a filter:

```python
import hashlib

class Filter:
    def inlet(self, body: dict, __user__: dict = None) -> dict:
        if __user__ and __user__.get("id"):
            body["safety_identifier"] = hashlib.sha256(
                __user__["id"].encode()
            ).hexdigest()
        return body
```

The hashed user UUID is added as a top-level body parameter and forwarded directly to OpenAI's API — no PII is sent, just an opaque hash.

:::warning Cannot Inject HTTP Headers
Filters can only modify the **request body** (`form_data`). Outbound HTTP headers are constructed separately and cannot be influenced from a filter. To add custom headers to API requests, use the **Admin Panel → Settings → Connections → OpenAI API** headers configuration.
:::

---

### 🔍 Resolving the Base Model (`__model__`)

When a user selects a workspace or custom model, `body["model"]` contains the custom model ID (e.g. `"my-custom-gpt5"`), not the underlying base model. To discover the actual base model, use the `__model__` dunder parameter:

```python
class Filter:
    def inlet(self, body: dict, __model__: dict = None) -> dict:
        custom_model_id = body["model"]  # e.g. "my-custom-gpt5"

        base_model_id = None
        if __model__ and "info" in __model__:
            base_model_id = __model__["info"].get("base_model_id")
            # e.g. "gpt-5.2"

        if base_model_id:
            print(f"Workspace model '{custom_model_id}' → base model '{base_model_id}'")
        else:
            print(f"Direct base model: '{custom_model_id}'")

        return body
```

If no `base_model_id` is present, the user selected a base model directly (no workspace wrapper).

#### Available Dunder Parameters

Filters can declare any of these parameters in their function signature to receive them automatically:

| Parameter | What it provides |
|-----------|-----------------|
| `__model__` | Full model dict (including `info.base_model_id` for workspace models) |
| `__user__` | User data (`id`, `email`, `name`, `role`) |
| `__metadata__` | Request metadata (`chat_id`, `session_id`, `interface`, etc.) |
| `__event_emitter__` | Function to send status updates, embeds, etc. to the client |
| `__chat_id__` | Chat session ID |
| `__request__` | The raw FastAPI `Request` object |

Only parameters you declare in your function signature are injected — Open WebUI inspects the signature at runtime to determine what to pass.

---

### 🎨 UI Indicators & Visual Feedback

#### In the Admin Functions Panel

| Indicator | Meaning |
|-----------|---------|
| 🟢 Green toggle | Filter is active (`is_active=True`) |
| ⚪ Grey toggle | Filter is inactive (`is_active=False`) |
| 🌐 Highlighted globe | Filter is global (`is_global=True`) |
| 🌐 Unhighlighted globe | Filter is not global (`is_global=False`) |

#### In Model Settings (FiltersSelector)

| State | Checkbox | Description |
|-------|----------|-------------|
| Global Filter | ✅ Checked & Disabled (greyed) | "This filter is globally enabled" |
| Selected Filter | ✅ Checked & Enabled | "This filter is selected for this model" |
| Unselected Filter | ☐ Unchecked & Enabled | "Click to include this filter" |

#### In Chat UI (Integrations Menu)

| Element | Description |
|---------|-------------|
| Filter name | Shows the filter's display name |
| Custom icon | SVG icon from `self.icon` (if provided) |
| Toggle switch | Enable/disable the filter for this chat |
| Status badge | Shows if filter is currently active |

---

### 💡 Best Practices for Filter Configuration

#### 1. When to Use Global Filters

✅ **Use global filters for:**
- Security and compliance (PII scrubbing, content moderation)
- System-wide formatting (standardize all outputs)
- Logging and analytics (track all requests)
- Organization-wide policies (enforce company guidelines)

❌ **Don't use global filters for:**
- Optional features (use toggleable filters instead)
- Model-specific behavior (use model-specific filters)
- User-preference features (let users control via toggles)

#### 2. When to Use Toggleable Filters

✅ **Make a filter toggleable (`toggle=True`) when:**
- Users should control when it's active (web search, translation)
- It's an optional enhancement (citation mode, verbose output)
- It adds functionality users may not always want (code formatting)
- It has a performance cost that should be optional

❌ **Don't make a filter toggleable when:**
- It's required for security/compliance (always-on is better)
- Users shouldn't be able to disable it (use always-on)
- It's a system-level transformation (global is better)

#### 3. Organizing Filters for Your Organization

**Recommended Structure:**

```
Global Always-On Filters:
├─ PII Scrubber (security)
├─ Content Moderator (compliance)
└─ Request Logger (analytics)

Model-Specific Always-On Filters:
├─ Code Formatter (for coding models only)
├─ Medical Terminology Corrector (for medical models)
└─ Legal Citation Validator (for legal models)

Toggleable Filters (User Choice):
├─ Web Search Integration
├─ Citation Mode
├─ Translation Filter
├─ Verbose Output Mode
└─ Image Description Generator
```

---

### 🎯 Key Components Explained

#### 1️⃣ **`Valves` Class (Optional Settings)**

Think of **Valves** as the knobs and sliders for your filter. If you want to give users configurable options to adjust your Filter’s behavior, you define those here.

```python
class Valves(BaseModel):
    OPTION_NAME: str = "Default Value"
```

For example:
If you're creating a filter that converts responses into uppercase, you might allow users to configure whether every output gets totally capitalized via a valve like `TRANSFORM_UPPERCASE: bool = True/False`.

##### Configuring Valves with Dropdown Menus (Enums)

You can enhance the user experience for your filter's settings by providing dropdown menus instead of free-form text inputs for certain `Valves`. This is achieved using `json_schema_extra` with the `enum` keyword in your Pydantic `Field` definitions.

The `enum` keyword allows you to specify a list of predefined values that the UI should present as options in a dropdown.

**Example:** Creating a dropdown for color themes in a filter.

```python
from pydantic import BaseModel, Field
from typing import Optional

# Define your available options (e.g., color themes)
COLOR_THEMES = {
    "Plain (No Color)": [],
    "Monochromatic Blue": ["blue", "RoyalBlue", "SteelBlue", "LightSteelBlue"],
    "Warm & Energetic": ["orange", "red", "magenta", "DarkOrange"],
    "Cool & Calm": ["cyan", "blue", "green", "Teal", "CadetBlue"],
    "Forest & Earth": ["green", "DarkGreen", "LimeGreen", "OliveGreen"],
    "Mystical Purple": ["purple", "DarkOrchid", "MediumPurple", "Lavender"],
    "Grayscale": ["gray", "DarkGray", "LightGray"],
    "Rainbow Fun": [
        "red",
        "orange",
        "yellow",
        "green",
        "blue",
        "indigo",
        "violet",
    ],
    "Ocean Breeze": ["blue", "cyan", "LightCyan", "DarkTurquoise"],
    "Sunset Glow": ["DarkRed", "DarkOrange", "Orange", "gold"],
    "Custom Sequence (See Code)": [],
}

class Filter:
    class Valves(BaseModel):
        selected_theme: str = Field(
            "Monochromatic Blue",
            description="Choose a predefined color theme for LLM responses. 'Plain (No Color)' disables coloring.",
            json_schema_extra={"enum": list(COLOR_THEMES.keys())}, # KEY: This creates the dropdown
        )
        custom_colors_csv: str = Field(
            "",
            description="CSV of colors for 'Custom Sequence' theme (e.g., 'red,blue,green'). Uses xcolor names.",
        )
        strip_existing_latex: bool = Field(
            True,
            description="If true, attempts to remove existing LaTeX color commands. Recommended to avoid nested rendering issues.",
        )
        colorize_type: str = Field(
            "sequential_word",
            description="How to apply colors: 'sequential_word' (word by word), 'sequential_line' (line by line), 'per_letter' (letter by letter), 'full_message' (entire message).",
            json_schema_extra={
                "enum": [
                    "sequential_word",
                    "sequential_line",
                    "per_letter",
                    "full_message",
                ]
            }, # Another example of an enum dropdown
        )
        color_cycle_reset_per_message: bool = Field(
            True,
            description="If true, the color sequence restarts for each new LLM response message. If false, it continues across messages.",
        )
        debug_logging: bool = Field(
            False,
            description="Enable verbose logging to the console for debugging filter operations.",
        )

    def __init__(self):
        self.valves = self.Valves()
        # ... rest of your __init__ logic ...
```

**What's happening?**

*   **`json_schema_extra`**: This argument in `Field` allows you to inject arbitrary JSON Schema properties that Pydantic doesn't explicitly support but can be used by downstream tools (like Open WebUI's UI renderer).
*   **`"enum": list(COLOR_THEMES.keys())`**: This tells Open WebUI that the `selected_theme` field should present a selection of values, specifically the keys from our `COLOR_THEMES` dictionary. The UI will then render a dropdown menu with "Plain (No Color)", "Monochromatic Blue", "Warm & Energetic", etc., as selectable options.
*   The `colorize_type` field also demonstrates another `enum` dropdown for different coloring methods.

Using `enum` for your `Valves` options makes your filters more user-friendly and prevents invalid inputs, leading to a smoother configuration experience.

---

#### 2️⃣ **`inlet` Function (Input Pre-Processing)**

The `inlet` function is like **prepping food before cooking**. Imagine you’re a chef: before the ingredients go into the recipe (the LLM in this case), you might wash vegetables, chop onions, or season the meat. Without this step, your final dish could lack flavor, have unwashed produce, or simply be inconsistent.

In the world of Open WebUI, the `inlet` function does this important prep work on the **user input** before it’s sent to the model. It ensures the input is as clean, contextual, and helpful as possible for the AI to handle.

📥 **Input**:
- **`body`**: The raw input from Open WebUI to the model. It is in the format of a chat-completion request (usually a dictionary that includes fields like the conversation's messages, model settings, and other metadata). Think of this as your recipe ingredients.

🚀 **Your Task**:
Modify and return the `body`. The modified version of the `body` is what the LLM works with, so this is your chance to bring clarity, structure, and context to the input.

##### 🍳 Why Would You Use the `inlet`?
1. **Adding Context**: Automatically append crucial information to the user’s input, especially if their text is vague or incomplete. For example, you might add "You are a friendly assistant" or "Help this user troubleshoot a software bug."

2. **Formatting Data**: If the input requires a specific format, like JSON or Markdown, you can transform it before sending it to the model.

3. **Sanitizing Input**: Remove unwanted characters, strip potentially harmful or confusing symbols (like excessive whitespace or emojis), or replace sensitive information.

4. **Streamlining User Input**: If your model’s output improves with additional guidance, you can use the `inlet` to inject clarifying instructions automatically!

5. **Rate Limiting**: Track requests per user and reject requests that exceed your quota (works for both WebUI and API requests).

6. **Request Logging**: Log all incoming requests for analytics, debugging, or billing purposes.

7. **Language Detection**: Detect the user's language and inject translation instructions or route to a language-specific model.

8. **Prompt Injection Detection**: Scan user input for attempts to manipulate the system prompt and block malicious requests.

9. **Cost Estimation**: Estimate input tokens before sending to the model for budget tracking.

10. **A/B Testing**: Route users to different model configurations based on user ID or random selection.

##### 💡 Example Use Cases: Build on Food Prep

###### 🥗 Example 1: Adding System Context
Let’s say the LLM is a chef preparing a dish for Italian cuisine, but the user hasn’t mentioned "This is for Italian cooking." You can ensure the message is clear by appending this context before sending the data to the model.

```python
def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
    # Add system message for Italian context in the conversation
    context_message = {
        "role": "system",
        "content": "You are helping the user prepare an Italian meal."
    }
    # Insert the context at the beginning of the chat history
    body.setdefault("messages", []).insert(0, context_message)
    return body
```

📖 **What Happens?**
- Any user input like "What are some good dinner ideas?" now carries the Italian theme because we’ve set the system context! Cheesecake might not show up as an answer, but pasta sure will.

###### 🔪 Example 2: Cleaning Input (Remove Odd Characters)
Suppose the input from the user looks messy or includes unwanted symbols like `!!!`, making the conversation inefficient or harder for the model to parse. You can clean it up while preserving the core content.

```python
def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
    # Clean the last user input (from the end of the 'messages' list)
    last_message = body["messages"][-1]["content"]
    body["messages"][-1]["content"] = last_message.replace("!!!", "").strip()
    return body
```

📖 **What Happens?**
- Before: `"How can I debug this issue!!!"` ➡️ Sent to the model as `"How can I debug this issue"`

:::note

Note: The user feels the same, but the model processes a cleaner and easier-to-understand query.

:::

##### 📊 How `inlet` Helps Optimize Input for the LLM:
- Improves **accuracy** by clarifying ambiguous queries.
- Makes the AI **more efficient** by removing unnecessary noise like emojis, HTML tags, or extra punctuation.
- Ensures **consistency** by formatting user input to match the model’s expected patterns or schemas (like, say, JSON for a specific use case).

💭 **Think of `inlet` as the sous-chef in your kitchen**—ensuring everything that goes into the model (your AI "recipe") has been prepped, cleaned, and seasoned to perfection. The better the input, the better the output!

---

#### 🆕 3️⃣ **`stream` Hook (New in Open WebUI 0.5.17)**

##### 🔄 What is the `stream` Hook?
The **`stream` function** is a new feature introduced in Open WebUI **0.5.17** that allows you to **intercept and modify streamed model responses** in real time.

Unlike `outlet`, which processes an entire completed response, `stream` operates on **individual chunks** as they are received from the model.

##### 🛠️ When to Use the Stream Hook?
- **Real-time content filtering** - Censor profanity or sensitive content as it streams
- **Live word replacement** - Replace brand names, competitor mentions, or outdated terms
- **Streaming analytics** - Count tokens and track response length in real-time
- **Progress indicators** - Detect specific patterns to show loading states
- **Debugging** - Log each chunk for troubleshooting streaming issues
- **Format correction** - Fix common formatting issues as they appear

##### 📜 Example: Logging Streaming Chunks

Here’s how you can inspect and modify streamed LLM responses:
```python
def stream(self, event: dict) -> dict:
    print(event)  # Print each incoming chunk for inspection
    return event
```

> **Example Streamed Events:**
```jsonl
{"id": "chatcmpl-B4l99MMaP3QLGU5uV7BaBM0eDS0jb","choices": [{"delta": {"content": "Hi"}}]}
{"id": "chatcmpl-B4l99MMaP3QLGU5uV7BaBM0eDS0jb","choices": [{"delta": {"content": "!"}}]}
{"id": "chatcmpl-B4l99MMaP3QLGU5uV7BaBM0eDS0jb","choices": [{"delta": {"content": " 😊"}}]}
```
📖 **What Happens?**
- Each line represents a **small fragment** of the model's streamed response.
- The **`delta.content` field** contains the progressively generated text.

##### 🔄 Example: Filtering Out Emojis from Streamed Data
```python
def stream(self, event: dict) -> dict:
    for choice in event.get("choices", []):
        delta = choice.get("delta", {})
        if "content" in delta:
            delta["content"] = delta["content"].replace("😊", "")  # Strip emojis
    return event
```
📖 **Before:** `"Hi 😊"`
📖 **After:** `"Hi"`

---

#### 4️⃣ **`outlet` Function (Output Post-Processing)**

The `outlet` function is like a **proofreader**: tidy up the AI's response (or make final changes) *after it’s processed by the LLM.*

📤 **Input**:
- **`body`**: This contains **all current messages** in the chat (user history + LLM replies).

🚀 **Your Task**: Modify this `body`. You can clean, append, or log changes, but be mindful of how each adjustment impacts the user experience.

💡 **Best Practices**:
- Prefer logging over direct edits in the outlet (e.g., for debugging or analytics).
- If heavy modifications are needed (like formatting outputs), consider using the **pipe function** instead.

##### 🛠️ Use Cases for `outlet`:
- **Response logging** - Track all model outputs for analytics or compliance
- **Token usage tracking** - Count output tokens after completion for billing
- **Langfuse/observability integration** - Send traces to monitoring platforms
- **Citation formatting** - Reformat reference links in the final output
- **Disclaimer injection** - Append legal notices or AI disclosure statements
- **Response caching** - Store responses for future retrieval
- **Quality scoring** - Run automated quality checks on model outputs

:::warning Outlet and API Requests
Remember: `outlet()` is **not called** for direct API requests to `/api/chat/completions`. If you need outlet processing for API calls, see the [Filter Behavior with API Requests](#-filter-behavior-with-api-requests) section above.
:::

💡 **Example Use Case**: Strip out sensitive API responses you don't want the user to see:
```python
def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
    for message in body["messages"]:
        message["content"] = message["content"].replace("<API_KEY>", "[REDACTED]")
    return body
```

---

## 🌟 Filters in Action: Building Practical Examples

Let’s build some real-world examples to see how you’d use Filters!

### 📚 Example #1: Add Context to Every User Input

Want the LLM to always know it's assisting a customer in troubleshooting software bugs? You can add instructions like **"You're a software troubleshooting assistant"** to every user query.

```python
class Filter:
    def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        context_message = {
            "role": "system",
            "content": "You're a software troubleshooting assistant."
        }
        body.setdefault("messages", []).insert(0, context_message)
        return body
```

---

### 📚 Example #2: Highlight Outputs for Easy Reading

Returning output in Markdown or another formatted style? Use the `outlet` function!

```python
class Filter:
    def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        # Add "highlight" markdown for every response
        for message in body["messages"]:
            if message["role"] == "assistant":  # Target model response
                message["content"] = f"**{message['content']}**"  # Highlight with Markdown
        return body
```

---

## 🚧 Potential Confusion: Clear FAQ 🛑

### **Q: How Are Filters Different From Pipe Functions?**

Filters modify data **going to** and **coming from models** but do not significantly interact with logic outside of these phases. Pipes, on the other hand:
- Can integrate **external APIs** or significantly transform how the backend handles operations.
- Expose custom logic as entirely new "models."

### **Q: Can I Do Heavy Post-Processing Inside `outlet`?**

You can, but **it’s not the best practice.**:
- **Filters** are designed to make lightweight changes or apply logging.
- If heavy modifications are required, consider a **Pipe Function** instead.

---

## 🎉 Recap: Why Build Filter Functions?

By now, you’ve learned:
1. **Inlet** manipulates **user inputs** (pre-processing).
2. **Stream** intercepts and modifies **streamed model outputs** (real-time).
3. **Outlet** tweaks **AI outputs** (post-processing).
4. Filters are best for lightweight, real-time alterations to the data flow.
5. With **Valves**, you empower users to configure Filters dynamically for tailored behavior.

---

🚀 **Your Turn**: Start experimenting! What small tweak or context addition could elevate your Open WebUI experience? Filters are fun to build, flexible to use, and can take your models to the next level!

Happy coding! ✨

---
sidebar_position: 4
title: "Pipe Function"
---

# 🚰 Pipe Function: Create Custom "Agents/Models"
Welcome to this guide on creating **Pipes** in Open WebUI! Think of Pipes as a way to **adding** a new model to Open WebUI. In this document, we'll break down what a Pipe is, how it works, and how you can create your own to add custom logic and processing to your Open WebUI models. We'll use clear metaphors and go through every detail to ensure you have a comprehensive understanding.

:::warning Use Async Functions for Future Compatibility
Pipe functions should generally be defined as `async` to ensure compatibility with future Open WebUI versions. The backend is progressively moving toward fully async execution, and synchronous functions may block execution or cause issues in future releases. When in doubt, make your `pipe` function async.
:::

## Introduction to Pipes

Imagine Open WebUI as a **plumbing system** where data flows through pipes and valves. In this analogy:

- **Pipes** are like **plugins** that let you introduce new pathways for data to flow, allowing you to inject custom logic and processing.
- **Valves** are the **configurable parts** of your pipe that control how data flows through it.

By creating a Pipe, you're essentially crafting a custom model with the specific behavior you want, all within the Open WebUI framework.

---

## Understanding the Pipe Structure

Let's start with a basic, barebones version of a Pipe to understand its structure:

```python
from pydantic import BaseModel, Field

class Pipe:
    class Valves(BaseModel):
        MODEL_ID: str = Field(default="")

    def __init__(self):
        self.valves = self.Valves()

    async def pipe(self, body: dict):
        # Logic goes here
        print(self.valves, body)  # This will print the configuration options and the input body
        return "Hello, World!"
```

### The Pipe Class

- **Definition**: The `Pipe` class is where you define your custom logic.
- **Purpose**: Acts as the blueprint for your plugin, determining how it behaves within Open WebUI.

### Valves: Configuring Your Pipe

- **Definition**: `Valves` is a nested class within `Pipe`, inheriting from `BaseModel`.
- **Purpose**: It contains the configuration options (parameters) that persist across the use of your Pipe.
- **Example**: In the above code, `MODEL_ID` is a configuration option with a default empty string.

**Metaphor**: Think of Valves as the knobs on a real-world pipe system that control the flow of water. In your Pipe, Valves allow users to adjust settings that influence how the data flows and is processed.

### The `__init__` Method

- **Definition**: The constructor method for the `Pipe` class.
- **Purpose**: Initializes the Pipe's state and sets up any necessary components.
- **Best Practice**: Keep it simple; primarily initialize `self.valves` here.

```python
def __init__(self):
    self.valves = self.Valves()
```

### The `pipe` Function

- **Definition**: The core function where your custom logic resides.
- **Parameters**:
  - `body`: A dictionary containing the input data.
- **Purpose**: Processes the input data using your custom logic and returns the result.

```python
async def pipe(self, body: dict):
    # Logic goes here
    print(self.valves, body)  # This will print the configuration options and the input body
    return "Hello, World!"
```

**Note**: Always place `Valves` at the top of your `Pipe` class, followed by `__init__`, and then the `pipe` function. This structure ensures clarity and consistency.

---

## Creating Multiple Models with Pipes

What if you want your Pipe to create **multiple models** within Open WebUI? You can achieve this by defining a `pipes` function or variable inside your `Pipe` class. This setup, informally called a **manifold**, allows your Pipe to represent multiple models.

Here's how you can do it:

```python
from pydantic import BaseModel, Field

class Pipe:
    class Valves(BaseModel):
        MODEL_ID: str = Field(default="")

    def __init__(self):
        self.valves = self.Valves()

    def pipes(self):
        return [
            {"id": "model_id_1", "name": "model_1"},
            {"id": "model_id_2", "name": "model_2"},
            {"id": "model_id_3", "name": "model_3"},
        ]

    async def pipe(self, body: dict):
        # Logic goes here
        print(self.valves, body)  # Prints the configuration options and the input body
        model = body.get("model", "")
        return f"{model}: Hello, World!"
```

### Explanation

- **`pipes` Function**:
  - Returns a list of dictionaries.
  - Each dictionary represents a model with unique `id` and `name` keys.
  - These models will show up individually in the Open WebUI model selector.

- **Updated `pipe` Function**:
  - Processes input based on the selected model.
  - In this example, it includes the model name in the returned string.

---

## Example: OpenAI Proxy Pipe

Let's dive into a practical example where we'll create a Pipe that proxies requests to the OpenAI API. This Pipe will fetch available models from OpenAI and allow users to interact with them through Open WebUI.

```python
from pydantic import BaseModel, Field
import requests

class Pipe:
    class Valves(BaseModel):
        NAME_PREFIX: str = Field(
            default="OPENAI/",
            description="Prefix to be added before model names.",
        )
        OPENAI_API_BASE_URL: str = Field(
            default="https://api.openai.com/v1",
            description="Base URL for accessing OpenAI API endpoints.",
        )
        OPENAI_API_KEY: str = Field(
            default="",
            description="API key for authenticating requests to the OpenAI API.",
        )

    def __init__(self):
        self.valves = self.Valves()

    def pipes(self):
        if self.valves.OPENAI_API_KEY:
            try:
                headers = {
                    "Authorization": f"Bearer {self.valves.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                }

                r = requests.get(
                    f"{self.valves.OPENAI_API_BASE_URL}/models", headers=headers
                )
                models = r.json()
                return [
                    {
                        "id": model["id"],
                        "name": f'{self.valves.NAME_PREFIX}{model.get("name", model["id"])}',
                    }
                    for model in models["data"]
                    if "gpt" in model["id"]
                ]

            except Exception as e:
                return [
                    {
                        "id": "error",
                        "name": "Error fetching models. Please check your API Key.",
                    },
                ]
        else:
            return [
                {
                    "id": "error",
                    "name": "API Key not provided.",
                },
            ]

    def pipe(self, body: dict, __user__: dict):
        print(f"pipe:{__name__}")
        headers = {
            "Authorization": f"Bearer {self.valves.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        # Extract model id from the model name
        model_id = body["model"][body["model"].find(".") + 1 :]

        # Update the model id in the body
        payload = {**body, "model": model_id}
        try:
            r = requests.post(
                url=f"{self.valves.OPENAI_API_BASE_URL}/chat/completions",
                json=payload,
                headers=headers,
                stream=True,
            )

            r.raise_for_status()

            if body.get("stream", False):
                return r.iter_lines()
            else:
                return r.json()
        except Exception as e:
            return f"Error: {e}"
```

### Detailed Breakdown

#### Valves Configuration

- **`NAME_PREFIX`**:
  - Adds a prefix to the model names displayed in Open WebUI.
  - Default: `"OPENAI/"`.
- **`OPENAI_API_BASE_URL`**:
  - Specifies the base URL for the OpenAI API.
  - Default: `"https://api.openai.com/v1"`.
- **`OPENAI_API_KEY`**:
  - Your OpenAI API key for authentication.
  - Default: `""` (empty string; must be provided).

#### The `pipes` Function

- **Purpose**: Fetches available OpenAI models and makes them accessible in Open WebUI.

- **Process**:
  1. **Check for API Key**: Ensures that an API key is provided.
  2. **Fetch Models**: Makes a GET request to the OpenAI API to retrieve available models.
  3. **Filter Models**: Returns models that have `"gpt"` in their `id`.
  4. **Error Handling**: If there's an issue, returns an error message.

- **Return Format**: A list of dictionaries with `id` and `name` for each model.

#### The `pipe` Function

- **Purpose**: Handles the request to the selected OpenAI model and returns the response.

- **Parameters**:
  - `body`: Contains the request data.
  - `__user__`: Contains user information (not used in this example but can be useful for authentication or logging).

- **Process**:
  1. **Prepare Headers**: Sets up the headers with the API key and content type.
  2. **Extract Model ID**: Extracts the actual model ID from the selected model name.
  3. **Prepare Payload**: Updates the body with the correct model ID.
  4. **Make API Request**: Sends a POST request to the OpenAI API's chat completions endpoint.
  5. **Handle Streaming**: If `stream` is `True`, returns an iterable of lines.
  6. **Error Handling**: Catches exceptions and returns an error message.

### Extending the Proxy Pipe

You can modify this proxy Pipe to support additional service providers like Anthropic, Perplexity, and more by adjusting the API endpoints, headers, and logic within the `pipes` and `pipe` functions.

---

## Using Internal Open WebUI Functions

Sometimes, you may want to leverage the internal functions of Open WebUI within your Pipe. You can import these functions directly from the `open_webui` package. Keep in mind that while unlikely, internal functions may change for optimization purposes, so always refer to the latest documentation.

Here's how you can use internal Open WebUI functions:

```python
from pydantic import BaseModel, Field
from fastapi import Request

from open_webui.models.users import Users
from open_webui.utils.chat import generate_chat_completion

class Pipe:
    def __init__(self):
        pass

    async def pipe(
        self,
        body: dict,
        __user__: dict,
        __request__: Request,
    ) -> str:
        # Use the unified endpoint with the updated signature
        user = Users.get_user_by_id(__user__["id"])
        body["model"] = "llama3.2:latest"
        return await generate_chat_completion(__request__, body, user)
```

### Explanation

- **Imports**:
  - `Users` from `open_webui.models.users`: To fetch user information.
  - `generate_chat_completion` from `open_webui.utils.chat`: To generate chat completions using internal logic.

- **Asynchronous `pipe` Function**:
  - **Parameters**:
    - `body`: Input data for the model.
    - `__user__`: Dictionary containing user information.
    - `__request__`: The request object from FastAPI (required by `generate_chat_completion`).
  - **Process**:
    1. **Fetch User Object**: Retrieves the user object using their ID.
    2. **Set Model**: Specifies the model to be used.
    3. **Generate Completion**: Calls `generate_chat_completion` to process the input and produce an output.

### Important Notes

- **Function Signatures**: Refer to the latest Open WebUI codebase or documentation for the most accurate function signatures and parameters.
- **Best Practices**: Always handle exceptions and errors gracefully to ensure a smooth user experience.

---

## Frequently Asked Questions

### Q1: Why should I use Pipes in Open WebUI?

**A**: Pipes allow you to add new "model" with custom logic and processing to Open WebUI. It's a flexible plugin system that lets you integrate external APIs, customize model behaviors, and create innovative features without altering the core codebase.

---

### Q2: What are Valves, and why are they important?

**A**: Valves are the configurable parameters of your Pipe. They function like settings or controls that determine how your Pipe operates. By adjusting Valves, you can change the behavior of your Pipe without modifying the underlying code.

---

### Q3: Can I create a Pipe without Valves?

**A**: Yes, you can create a simple Pipe without defining a Valves class if your Pipe doesn't require any persistent configuration options. However, including Valves is a good practice for flexibility and future scalability.

---

### Q4: How do I ensure my Pipe is secure when using API keys?

**A**: Never hard-code sensitive information like API keys into your Pipe. Instead, use Valves to input and store API keys securely. Ensure that your code handles these keys appropriately and avoids logging or exposing them.

---

### Q5: What is the difference between the `pipe` and `pipes` functions?

**A**:

- **`pipe` Function**: The primary function where you process the input data and generate an output. It handles the logic for a single model.

- **`pipes` Function**: Allows your Pipe to represent multiple models by returning a list of model definitions. Each model will appear individually in Open WebUI.

---

### Q6: How can I handle errors in my Pipe?

**A**: Use try-except blocks within your `pipe` and `pipes` functions to catch exceptions. Return meaningful error messages or handle the errors gracefully to ensure the user is informed about what went wrong.

---

### Q7: Can I use external libraries in my Pipe?

**A**: Yes, you can import and use external libraries as needed. Ensure that any dependencies are properly installed and managed within your environment.

---

### Q8: How do I test my Pipe?

**A**: Test your Pipe by running Open WebUI in a development environment and selecting your custom model from the interface. Validate that your Pipe behaves as expected with various inputs and configurations.

---

### Q9: Are there any best practices for organizing my Pipe's code?

**A**: Yes, follow these guidelines:

- Keep `Valves` at the top of your `Pipe` class.
- Initialize variables in the `__init__` method, primarily `self.valves`.
- Place the `pipe` function after the `__init__` method.
- Use clear and descriptive variable names.
- Comment your code for clarity.

---

### Q10: Where can I find the latest Open WebUI documentation?

**A**: Visit the official Open WebUI repository or documentation site for the most up-to-date information, including function signatures, examples, and migration guides if any changes occur.

---

## Conclusion

By now, you should have a thorough understanding of how to create and use Pipes in Open WebUI. Pipes offer a powerful way to extend and customize the capabilities of Open WebUI to suit your specific needs. Whether you're integrating external APIs, adding new models, or injecting complex logic, Pipes provide the flexibility to make it happen.

Remember to:

- **Use clear and consistent structure** in your Pipe classes.
- **Leverage Valves** for configurable options.
- **Handle errors gracefully** to improve the user experience.
- **Consult the latest documentation** for any updates or changes.

Happy coding, and enjoy extending your Open WebUI with Pipes!

---
sidebar_position: 1
title: "Tools"
---

# What are Tools?

⚙️ Tools are the various ways you can extend an LLM's capabilities beyond simple text generation. When enabled, they allow your chatbot to do amazing things — like search the web, scrape data, generate images, talk back using AI voices, and more.

Because there are several ways to integrate "Tools" in Open WebUI, it's important to understand which type you are using.

---

## Tooling Taxonomy: Which "Tool" are you using?

🧩 Users often encounter the term "Tools" in different contexts. Here is how to distinguish them:

| Type | Location in UI | Best For... | Source |
| :--- | :--- | :--- | :--- |
| **Native Features** | Admin/Settings | Core platform functionality | Built-in to Open WebUI |
| **Workspace Tools** | `Workspace > Tools` | User-created or community Python scripts | [Community Library](https://openwebui.com/search) |
| **Native MCP (HTTP)** | `Settings > Connections` | Standard MCP servers reachable via HTTP/SSE | External MCP Servers |
| **MCP via Proxy (MCPO)** | `Settings > Connections` | Local stdio-based MCP servers (e.g., Claude Desktop tools) | [MCPO Adapter](https://github.com/open-webui/mcpo) |
| **OpenAPI Servers** | `Settings > Connections` | Standard REST/OpenAPI web services | External Web APIs |
| **Open Terminal** | `Settings > Integrations` | Full shell access in an isolated Docker container (always-on) | [Open Terminal](https://github.com/open-webui/open-terminal) |

### 1. Native Features (Built-in)
These are deeply integrated into Open WebUI and generally don't require external scripts.
- **Web Search**: Integrated via engines like SearXNG, Google, or Tavily.
- **URL Fetching**: Extract text content directly from websites using `#` or native tools.
- **Image Generation**: Integrated with DALL-E, ComfyUI, or Automatic1111.
- **Memory**: The ability for models to remember facts about you across chats.
- **RAG (Knowledge)**: The ability to query uploaded documents (`#`).

In [**Native Mode**](#built-in-system-tools-nativeagentic-mode), these features are exposed as **Tools** that the model can call independently.

### 2. Workspace Tools (Custom Plugins)
These are **Python scripts** that run directly within the Open WebUI environment.
- **Capability**: Can do anything Python can do (web scraping, complex math, API calls).
- **Access**: Managed via the `Workspace` menu. 
- **Safety**: Always review code before importing, as these run on your server.
- **⚠️ Security Warning**: Normal or untrusted users should **not** be given permission to access the Workspace Tools section. This access allows a user to upload and execute arbitrary Python code on your server, which could lead to a full system compromise.

### 3. MCP (Model Context Protocol)

🔌 MCP is an open standard that allows LLMs to interact with external data and tools.
- **Native HTTP MCP**: Open WebUI can connect directly to any MCP server that exposes an HTTP/SSE endpoint.
- **MCPO (Proxy)**: Most community MCP servers use `stdio` (local command line). To use these in Open WebUI, you use the [**MCPO Proxy**](../../plugin/tools/openapi-servers/mcp.mdx) to bridge the connection.

### 4. OpenAPI / Function Calling Servers
Generic web servers that provide an OpenAPI (`.json` or `.yaml`) specification. Open WebUI can ingest these specs and treat every endpoint as a tool.

---

## How to Install & Manage Workspace Tools

📦 Workspace Tools are the most common way to extend your instance with community features.

1. Go to [Community Tool Library](https://openwebui.com/search)
2. Choose a Tool, then click the **Get** button.
3. Enter your Open WebUI instance’s URL (e.g. `http://localhost:3000`).
4. Click **Import to WebUI**.

:::warning Safety Tip
Never import a Tool you don’t recognize or trust. These are Python scripts and might run unsafe code on your host system. **Crucially, ensure you only grant "Tool" permissions to trusted users**, as the ability to create or import tools is equivalent to the ability to run arbitrary code on the server.
:::

---

## How to Use Tools in Chat

🔧 Once installed or connected, here’s how to enable them for your conversations:

### Option 1: Enable on-the-fly (Specific Chat)
While chatting, click the **➕ (plus)** icon in the input area. You’ll see a list of available Tools — you can enable them specifically for that session.

### Option 2: Enable by Default (Global/Model Level)
1. Go to **Workspace ➡️ Models**.
2. Choose the model you’re using and click the ✏️ edit icon.
3. Scroll to the **Tools** section.
4. ✅ Check the Tools you want this model to always have access to by default.
5. Click **Save**.

You can also let your LLM auto-select the right Tools using the [**AutoTool Filter**](https://openwebui.com/f/hub/autotool_filter/).

---

## Tool Calling Modes: Default vs. Native

Open WebUI offers two distinct ways for models to interact with tools: a standard **Default Mode** and a high-performance **Native Mode (Agentic Mode)**. Choosing the right mode depends on your model's capabilities and your performance requirements.

### 🟡 Default Mode (Prompt-based) — Legacy

:::warning Legacy Mode
Default Mode is maintained purely for **backwards compatibility** with older or smaller models that lack native function-calling support. It is considered **legacy** and should not be used when your model supports native tool calling. New deployments should use **Native Mode** exclusively.
:::

In Default Mode, Open WebUI manages tool selection by injecting a specific prompt template that guides the model to output a tool request.
- **Compatibility**: Works with **practically any model**, including older or smaller local models that lack native function-calling support.
- **Flexibility**: Highly customizable via prompt templates.
- **Caveats**:
  - Can be slower (requires extra tokens) and less reliable for complex, multi-step tool chaining.
  - **Breaks KV cache**: The injected prompt changes every turn, preventing LLM engines from reusing cached key-value pairs. This increases latency and cost for every message in the conversation.
  - Does not support built-in system tools (memory, notes, channels, etc.).

### 🟢 Native Mode (Agentic Mode / System Function Calling) — Recommended
Native Mode (also called **Agentic Mode**) leverages the model's built-in capability to handle tool definitions and return structured tool calls (JSON). This is the **recommended mode** for all models that support it — which includes the vast majority of modern models (2024+).

:::warning Model Quality Matters
**Agentic tool calling requires high-quality models to work reliably.** While small local models may technically support function calling, they often struggle with the complex reasoning required for multi-step tool usage. For best results, use frontier models like **GPT-5**, **Claude 4.5 Sonnet**, **Gemini 3 Flash**, or **MiniMax M2.5**. Small local models may produce malformed JSON or fail to follow the strict state management required for agentic behavior.
:::

#### Why use Native Mode (Agentic Mode)?
- **Speed & Efficiency**: Lower latency as it avoids bulky prompt-based tool selection.
- **KV Cache Friendly**: Tool definitions are sent as structured parameters (not injected into the prompt), so they don't invalidate the KV cache between turns. This can significantly reduce latency and token costs.
- **Reliability**: Higher accuracy in following tool schemas (with quality models).
- **Multi-step Chaining**: Essential for **Agentic Research** and **Interleaved Thinking** where a model needs to call multiple tools in succession.
- **Autonomous Decision-Making**: Models can decide when to search, which tools to use, and how to combine results.
- **System Tools**: Only Native Mode unlocks the [built-in system tools](#built-in-system-tools-nativeagentic-mode) (memory, notes, knowledge, channels, etc.).

#### How to Enable Native Mode (Agentic Mode)
Native Mode can be enabled at two levels:

1.  **Global/Administrator Level (Recommended)**:
    *   Navigate to **Admin Panel > Settings > Models**.
    *   Scroll to **Model Specific Settings** for your target model.
    *   Under **Advanced Parameters**, find the **Function Calling** dropdown and select `Native`.
2.  **Per-Chat Basis**:
    *   Inside a chat, click the ⚙️ **Chat Controls** icon.
    *   Go to **Advanced Params** and set **Function Calling** to `Native`.

![Chat Controls](/images/features/plugin/tools/chat-controls.png)



#### Model Requirements & Caveats

:::tip Recommended Models for Agentic Mode
For reliable agentic tool calling, use high-tier frontier models:
- **GPT-5** (OpenAI)
- **Claude 4.5 Sonnet** (Anthropic)
- **Gemini 3 Flash** (Google)
- **MiniMax M2.5**

These models excel at multi-step reasoning, proper JSON formatting, and autonomous tool selection.
:::

- **Large Local Models**: Some large local models (e.g., Qwen 3 32B, Llama 3.3 70B) can work with Native Mode, but results vary significantly by model quality.
- **Small Local Models Warning**: **Small local models** (under 30B parameters) often struggle with Native Mode. They may produce malformed JSON, fail to follow strict state management, or make poor tool selection decisions. For these models, **Default Mode** is usually more reliable.

#### Known Model-Specific Issues

:::caution DeepSeek V3.2 Function Calling Issues
**DeepSeek V3.2** has known issues with native function calling that cause **reproducible failures**. Despite being a 600B+ parameter model, it often outputs malformed tool calls.

**The Problem**: DeepSeek V3.2 was trained using a proprietary format called **DSML (DeepSeek Markup Language)** for tool calls. When using native function calling, the model sometimes outputs raw DSML/XML-like syntax instead of proper JSON:
- `<functionInvoke name="fetch_url">` instead of valid JSON
- `<function_calls>` / `</function_calls>` tags in content
- Garbled hybrid text like `prominentfunction_cinvoke name="search_parameter`

**Why it happens**: This is heavily **model-dependent behavior induced during DeepSeek's fine-tuning process**. DeepSeek chose to train their model on DSML rather than standard OpenAI-style JSON tool calls. While inference providers (VertexAI, OpenRouter, etc.) attempt to intercept DSML blocks and convert them to OpenAI-style JSON, this translation layer is unreliable under certain conditions (streaming, high temperature, high concurrency, multi-turn conversations). **The primary responsibility lies with DeepSeek** for using a non-standard format that requires fragile translation.

**Known contributing factors**:
- Higher temperature values correlate with more malformed output
- Multi-round conversations (6-8+ turns) can cause the model to stop calling functions entirely
- Complex multi-step workflows (15-30 tool calls) may cause "schema drift" where argument formats degrade

**Workarounds**:
- **Use Default Mode** (prompt-based) instead of Native Mode for DeepSeek — this is the recommended approach
- Lower temperature when using tool calling
- Limit multi-round tool calling sessions
- Consider alternative models for agentic workflows

**This is a DeepSeek model/API issue**, not an Open WebUI issue. Open WebUI correctly sends tools in standard OpenAI format — the malformed output originates from DeepSeek's non-standard internal format.
:::

| Feature | Default Mode (Legacy) | Native Mode (Recommended) |
|:---|:---|:---|
| **Status** | Legacy / backwards compat | ✅ Recommended |
| **Latency** | Medium/High | Low |
| **KV Cache** | ❌ Can break cache | ✅ Cache-friendly |
| **Model Compatibility** | Universal | Requires Tool-Calling Support |
| **Logic** | Prompt-based (Open WebUI) | Model-native (API/Ollama) |
| **System Tools** | ❌ Not available | ✅ Full access |
| **Complex Chaining** | ⚠️ Limited | ✅ Excellent |

### Built-in System Tools (Native/Agentic Mode)

🛠️ When **Native Mode (Agentic Mode)** is enabled, Open WebUI automatically injects powerful system tools. This unlocks truly agentic behaviors where capable models (like GPT-5, Claude 4.5 Sonnet, Gemini 3 Flash, or MiniMax M2.5) can perform multi-step research, explore knowledge bases, or manage user memory autonomously.

| Tool | Purpose |
|------|---------|
| **Search & Web** | *Requires `ENABLE_WEB_SEARCH` enabled AND per-chat "Web Search" toggle enabled.* |
| `search_web` | Search the public web for information. Best for current events, external references, or topics not covered in internal documents. |
| `fetch_url` | Visits a URL and extracts text content via the Web Loader. |
| **Knowledge Base** | *Requires per-model "Knowledge Base" category enabled (default: on). Which tools are injected depends on whether the model has attached knowledge — see note below.* |
| `list_knowledge_bases` | List the user's accessible knowledge bases with file counts. **Use this first** to discover what knowledge is available. |
| `query_knowledge_bases` | Search KB *names and descriptions* by semantic similarity. Use to find which KB is relevant when you don't know which one to query. |
| `search_knowledge_bases` | Search knowledge bases by name/description (text match). |
| `query_knowledge_files` | Search *file contents* inside KBs using vector search. **This is your main tool for finding information.** When a KB is attached to the model, searches are automatically scoped to that KB. |
| `search_knowledge_files` | Search files across accessible knowledge bases by filename (not content). |
| `view_file` | Get the full content of any user-accessible file by its ID. Only injected when the model has attached knowledge files. |
| `view_knowledge_file` | Get the full content of a file from a knowledge base. |
| **Image Gen** | *Requires image generation enabled (per-tool) AND per-chat "Image Generation" toggle enabled.* |
| `generate_image` | Generates a new image based on a prompt. Requires `ENABLE_IMAGE_GENERATION`. |
| `edit_image` | Edits existing images based on a prompt and image URLs. Requires `ENABLE_IMAGE_EDIT`. |
| **Code Interpreter** | *Requires `ENABLE_CODE_INTERPRETER` enabled (default: on) AND per-chat "Code Interpreter" toggle enabled.* |
| `execute_code` | Executes code in a sandboxed environment and returns the output. |
| **Memory** | *Requires Memory feature enabled AND per-model "Memory" category enabled (default: on).* |
| `search_memories` | Searches the user's personal memory/personalization bank. |
| `add_memory` | Stores a new fact in the user's personalization memory. |
| `replace_memory_content` | Updates an existing memory record by its unique ID. |
| `delete_memory` | Deletes a memory by its ID. |
| `list_memories` | Lists all stored memories for the user with content and dates. |
| **Notes** | *Requires `ENABLE_NOTES` AND per-model "Notes" category enabled (default: on).* |
| `search_notes` | Search the user's notes by title and content. |
| `view_note` | Get the full markdown content of a specific note. |
| `write_note` | Create a new private note for the user. |
| `replace_note_content` | Update an existing note's content or title. |
| **Chat History** | *Requires per-model "Chat History" category enabled (default: on).* |
| `search_chats` | Simple text search across chat titles and message content. Returns matching chat IDs and snippets. |
| `view_chat` | Reads and returns the full message history of a specific chat by ID. |
| **Channels** | *Requires `ENABLE_CHANNELS` AND per-model "Channels" category enabled (default: on).* |
| `search_channels` | Find public or accessible channels by name/description. |
| `search_channel_messages` | Search for specific messages inside accessible channels. |
| `view_channel_message` | View a specific message or its details in a channel. |
| `view_channel_thread` | View a full message thread/replies in a channel. |
| **Skills** | *Requires per-model "Skills" category enabled (default: on).* |
| `view_skill` | Load the full instructions of a skill from the available skills manifest. |
| **Time Tools** | *Requires per-model "Time & Calculation" category enabled (default: on).* |
| `get_current_timestamp` | Get the current UTC Unix timestamp and ISO date. |
| `calculate_timestamp` | Calculate relative timestamps (e.g., "3 days ago"). |

#### Tool Reference

| Tool | Parameters | Output |
|------|------------|--------|
| **Search & Web** | | |
| `search_web` | `query` (required), `count` (default: 5) | Array of `{title, link, snippet}` |
| `fetch_url` | `url` (required) | Plain text content (max 50,000 chars) |
| **Knowledge Base** | | |
| `list_knowledge_bases` | `count` (default: 10), `skip` (default: 0) | Array of `{id, name, description, file_count}` |
| `query_knowledge_bases` | `query` (required), `count` (default: 5) | Array of `{id, name, description}` by similarity |
| `search_knowledge_bases` | `query` (required), `count` (default: 5), `skip` (default: 0) | Array of `{id, name, description, file_count}` |
| `query_knowledge_files` | `query` (required), `knowledge_ids` (optional), `count` (default: 5) | Array of `{id, filename, content_snippet, knowledge_id}` |
| `search_knowledge_files` | `query` (required), `knowledge_id` (optional), `count` (default: 5), `skip` (default: 0) | Array of `{id, filename, knowledge_id, knowledge_name}` |
| `view_file` | `file_id` (required) | `{id, filename, content, updated_at, created_at}` |
| `view_knowledge_file` | `file_id` (required) | `{id, filename, content}` |
| **Image Gen** | | |
| `generate_image` | `prompt` (required) | `{status, message, images}` — auto-displayed |
| `edit_image` | `prompt` (required), `image_urls` (required) | `{status, message, images}` — auto-displayed |
| **Code Interpreter** | | |
| `execute_code` | `language` (required), `code` (required) | `{output, status}` |
| **Memory** | | |
| `search_memories` | `query` (required), `count` (default: 5) | Array of `{id, date, content}` |
| `add_memory` | `content` (required) | `{status: "success", id}` |
| `replace_memory_content` | `memory_id` (required), `content` (required) | `{status: "success", id, content}` |
| `delete_memory` | `memory_id` (required) | `{status: "success", message}` |
| `list_memories` | None | Array of `{id, content, created_at, updated_at}` |
| **Notes** | | |
| `search_notes` | `query` (required), `count` (default: 5), `start_timestamp`, `end_timestamp` | Array of `{id, title, snippet, updated_at}` |
| `view_note` | `note_id` (required) | `{id, title, content, updated_at, created_at}` |
| `write_note` | `title` (required), `content` (required) | `{status: "success", id}` |
| `replace_note_content` | `note_id` (required), `content` (required), `title` (optional) | `{status: "success", id, title}` |
| **Chat History** | | |
| `search_chats` | `query` (required), `count` (default: 5), `start_timestamp`, `end_timestamp` | Array of `{id, title, snippet, updated_at}` |
| `view_chat` | `chat_id` (required) | `{id, title, messages: [{role, content}]}` |
| **Channels** | | |
| `search_channels` | `query` (required), `count` (default: 5) | Array of `{id, name, description}` |
| `search_channel_messages` | `query` (required), `count` (default: 10), `start_timestamp`, `end_timestamp` | Array of `{id, channel_id, content, user_name, created_at}` |
| `view_channel_message` | `message_id` (required) | `{id, content, user_name, created_at, reply_count}` |
| `view_channel_thread` | `parent_message_id` (required) | Array of `{id, content, user_name, created_at}` |
| **Skills** | | |
| `view_skill` | `name` (required) | `{name, content}` |
| **Time Tools** | | |
| `get_current_timestamp` | None | `{current_timestamp, current_iso}` |
| `calculate_timestamp` | `days_ago`, `weeks_ago`, `months_ago`, `years_ago` (all default: 0) | `{current_timestamp, current_iso, calculated_timestamp, calculated_iso}` |

:::info Automatic Timezone Detection
Open WebUI automatically detects and stores your timezone when you log in. This allows time-related tools and features to provide accurate local times without any manual configuration. Your timezone is determined from your browser settings.
:::

:::warning Knowledge Tools Change Based on Attached Knowledge
The set of knowledge tools injected into a model **changes depending on whether the model has knowledge attached** (via the Model Editor). These are **two mutually exclusive sets** — the model gets one or the other, never both.

**Model with attached knowledge** (files, collections, or notes):

| Tool | When Available |
|------|---------------|
| `query_knowledge_files` | Always (auto-scoped to attached KBs) |
| `view_file` | When attached knowledge includes files or collections |

The model **does not** get the browsing tools (`list_knowledge_bases`, `search_knowledge_bases`, etc.) because it doesn't need to discover KBs — the search is automatically scoped to its attachments.

**Model without attached knowledge** (general-purpose):

| Tool | Purpose |
|------|---------|
| `list_knowledge_bases` | Discover available KBs |
| `search_knowledge_bases` | Search KBs by name/description (text match) |
| `query_knowledge_bases` | Search KBs by name/description (semantic similarity) |
| `search_knowledge_files` | Search files by filename |
| `query_knowledge_files` | Search file contents (vector search) |
| `view_knowledge_file` | Read a full file from a KB |

This model has the full browsing set to autonomously discover and explore any KB the user has access to.
:::

:::caution Attached Knowledge Still Requires User Access
Attaching a knowledge base to a custom model does **not** bypass access control. When a user chats with the model, `query_knowledge_files` checks whether **that specific user** has permission to access each attached knowledge item. Items the user cannot access are silently excluded from search results.

**Access requirements by knowledge type:**

| Attached Type | User Needs |
|---------------|-----------|
| **Knowledge Base** (collection) | Owner, admin, or explicit read access grant |
| **Individual File** | Owner or admin only (no access grants) |
| **Note** | Owner, admin, or explicit read access grant |

**Example scenario**: An admin creates a private knowledge base and attaches it to a custom model shared with all users. Regular users chatting with this model will get **empty results** from `query_knowledge_files` because they don't have read access to the KB itself — even though they can use the model.

**Solution**: Make sure users who need access to the model's knowledge also have read access to the underlying knowledge base (via access grants or group permissions in the Knowledge settings).
:::

:::info Recommended KB Tool Workflow (No Attached Knowledge)
When using a model **without** attached knowledge:
1. First call `list_knowledge_bases` to discover what knowledge is available
2. Then use `query_knowledge_files` to search file contents within relevant KBs
3. If still empty, the files may not be embedded yet, or you may have **Full Context mode enabled** which bypasses the vector store

**Do NOT use Full Context mode with knowledge tools** — Full Context injects file content directly and doesn't store embeddings, so `query_knowledge_files` will return empty. Use Focused Retrieval (default) for tool-based access.
:::

:::tip Knowledge Base Tools vs RAG Pipeline
The native `query_knowledge_files` tool uses **simple vector search** with a default of 5 results. It does **not** use:
- Hybrid search (BM25 + vector)
- Reranking (external reranker endpoint)
- The "Top K Reranker" admin setting

For the full RAG pipeline with hybrid search and reranking, use the **File Context** capability (attach files via `#` or knowledge base assignment) instead of relying on autonomous tool calls.
:::

:::warning Knowledge is NOT Auto-Injected in Native Mode
**Important:** When using Native Function Calling, attached knowledge is **not automatically injected** into the conversation. The model must actively call knowledge tools to search and retrieve information.

**If your model isn't using attached knowledge:**
1. **Add instructions to your system prompt** telling the model to discover and query knowledge bases. Example: *"When users ask questions, first use list_knowledge_bases to see what knowledge is available, then use query_knowledge_files to search the relevant knowledge base before answering."*
2. **Or disable Native Function Calling** for that model to restore automatic RAG injection.
3. **Or use "Full Context" mode** for attached knowledge (click on the attachment and select "Use Entire Document") which always injects the full content.

See [Knowledge Scoping with Native Function Calling](/features/ai-knowledge/knowledge#knowledge-scoping-with-native-function-calling) for more details.
:::

**Why use these?** It allows for **Deep Research** (searching the web multiple times, or querying knowledge bases), **Contextual Awareness** (looking up previous chats or notes), **Dynamic Personalization** (saving facts), and **Precise Automation** (generating content based on existing notes or documents).

#### Disabling Builtin Tools (Per-Model)

The **Builtin Tools** capability can be toggled on or off for each model in the **Workspace > Models** editor under **Capabilities**. When enabled (the default), all the system tools listed above are automatically injected when using Native Mode.

**When to disable Builtin Tools:**

| Scenario | Reason to Disable |
|----------|-------------------|
| **Model doesn't support function calling** | Smaller or older models may not handle the `tools` parameter correctly |
| **Simpler/predictable behavior needed** | You want the model to work only with pre-injected context, no autonomous tool calls |
| **Security/control concerns** | Prevents the model from actively querying knowledge bases, searching chats, accessing memories, etc. |
| **Token efficiency** | Tool specifications consume tokens; disabling saves context window space |

**What happens when Builtin Tools is disabled:**

1. **No tool injection**: The model won't receive any of the built-in system tools, even in Native Mode.
2. **RAG still works** (if File Context is enabled): Attached files are still processed via RAG and injected as context.
3. **No autonomous retrieval**: The model cannot decide to search knowledge bases or fetch additional information—it works only with what's provided upfront.

#### Granular Builtin Tool Categories (Per-Model)

When the **Builtin Tools** capability is enabled, you can further control which **categories** of builtin tools are available to the model. This appears in the Model Editor as a set of checkboxes under **Builtin Tools**.

![Builtin Tools categories in the Model Editor](/images/features/plugin/tools/builtin_tools.png)

| Category | Tools Included | Description |
|----------|----------------|-------------|
| **Time & Calculation** | `get_current_timestamp`, `calculate_timestamp` | Get current time and perform date/time calculations |
| **Memory** | `search_memories`, `add_memory`, `replace_memory_content`, `delete_memory`, `list_memories` | Search and manage user memories |
| **Chat History** | `search_chats`, `view_chat` | Search and view user chat history |
| **Notes** | `search_notes`, `view_note`, `write_note`, `replace_note_content` | Search, view, and manage user notes |
| **Knowledge Base** | `list_knowledge_bases`, `search_knowledge_bases`, `query_knowledge_bases`, `search_knowledge_files`, `query_knowledge_files`, `view_knowledge_file` | Browse and query knowledge bases |
| **Web Search** | `search_web`, `fetch_url` | Search the web and fetch URL content |
| **Image Generation** | `generate_image`, `edit_image` | Generate and edit images |
| **Code Interpreter** | `execute_code` | Execute code in a sandboxed environment |
| **Channels** | `search_channels`, `search_channel_messages`, `view_channel_message`, `view_channel_thread` | Search channels and channel messages |
| **Skills** | `view_skill` | Load skill instructions on-demand from the manifest |

All categories are **enabled by default**. Disabling a category prevents those specific tools from being injected, while keeping other categories active.

**Use cases for granular control:**

| Scenario | Recommended Configuration |
|----------|---------------------------|
| **Privacy-focused model** | Disable Memory and Chat History to prevent access to personal data |
| **Read-only assistant** | Disable Notes (prevents creating/modifying notes) but keep Knowledge Base enabled |
| **Minimal token usage** | Enable only the categories the model actually needs |
| **Knowledge-centric bot** | Disable everything except Knowledge Base and Time |

:::note
These per-category toggles only appear when the main **Builtin Tools** capability is enabled. If you disable Builtin Tools entirely, no tools are injected regardless of category settings.
:::

:::info Global Features Take Precedence
Enabling a per-model category toggle does **not** override global feature flags. For example, if `ENABLE_NOTES` is disabled globally (Admin Panel), Notes tools will not be available even if the "Notes" category is enabled for the model. The per-model toggles only allow you to *further restrict* what's already available—they cannot enable features that are disabled at the global level.
:::

:::tip Per-Chat Feature Toggles (Web Search, Image Generation, Code Interpreter)
**Web Search**, **Image Generation**, and **Code Interpreter** built-in tools have an additional layer of control: the **per-chat feature toggle** in the chat input bar. For these tools to be injected in Native Mode, **all three conditions** must be met:

1. **Global config enabled** — the feature is turned on in Admin Panel (e.g., `ENABLE_WEB_SEARCH`)
2. **Model capability enabled** — the model has the capability checked in Workspace > Models (e.g., "Web Search")
3. **Per-chat toggle enabled** — the user has activated the feature for this specific chat via the chat input bar toggles

This means users can disable web search (or image generation, or code interpreter) on a per-conversation basis, even if it's enabled globally and on the model. This is useful for chats where information must stay offline or where you want to prevent unintended tool usage.
:::

:::tip Full Agentic Experience
For the best out-of-the-box agentic experience, administrators can enable **Web Search**, **Image Generation**, and **Code Interpreter** as default features for a model. In the **Admin Panel > Settings > Models**, find the **Model Specific Settings** for your target model and toggle these three on under **Default Features**. This ensures they are active in every new chat by default, so users get the full tool-calling experience without manually enabling each toggle. Users can still turn them off per-chat if needed.
:::

:::tip Builtin Tools vs File Context
**Builtin Tools** controls whether the model gets *tools* for autonomous retrieval. It does **not** control whether file content is injected via RAG—that's controlled by the separate **File Context** capability.

- **File Context** = Whether Open WebUI extracts and injects file content (RAG processing)
- **Builtin Tools** = Whether the model gets tools to autonomously search/retrieve additional content

See [File Context vs Builtin Tools](/features/chat-conversations/rag#file-context-vs-builtin-tools) for a detailed comparison.
:::

### Interleaved Thinking {#interleaved-thinking}

🧠 When using **Native Mode (Agentic Mode)**, high-tier models can engage in **Interleaved Thinking**. This is a powerful "Thought → Action → Thought → Action → Thought → ..." loop where the model can reason about a task, execute one or more tools, evaluate the results, and then decide on its next move.

:::info Quality Models Required
Interleaved thinking requires models with strong reasoning capabilities. This feature works best with frontier models (GPT-5, Claude 4.5+, Gemini 3+) that can maintain context across multiple tool calls and make intelligent decisions about which tools to use when.
:::

This is fundamentally different from a single-shot tool call. In an interleaved workflow, the model follows a cycle:
1.  **Reason**: Analyze the user's intent and identify information gaps.
2.  **Act**: Call a tool (e.g., `query_knowledge_files` for internal docs or `search_web` and `fetch_url` for web research).
3.  **Think**: Read the tool's output and update its internal understanding.
4.  **Iterate**: If the answer isn't clear, call another tool (e.g., `view_knowledge_file` to read a specific document or `fetch_url` to read a specific page) or refine the search.
5.  **Finalize**: Only after completing this "Deep Research" cycle does the model provide a final, grounded answer.

This behavior is what transforms a standard chatbot into an **Agentic AI** capable of solving complex, multi-step problems autonomously.

---

---

## 🚀 Summary & Next Steps

Tools bring your AI to life by giving it hands to interact with the world.
- **Browse Tools**: [openwebui.com/search](https://openwebui.com/search)
- **Advanced Setup**: Learn more about [MCP Support](./openapi-servers/mcp.mdx)
- **Development**: [Writing your own Custom Toolkits](./development.mdx)