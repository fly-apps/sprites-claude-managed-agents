# dispatch

This is the dispatcher running as a Fly App. When work is added to the queue, Anthropic calls the dispatcher's webhook to wake it up. The dispatcher wakes each Claude session's Sprite worker, creating new Sprites as needed.
