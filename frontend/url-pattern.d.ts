// Next 16.3 references these web names globally; Node 26 exports them in node:url.
import type {
  URLPatternInput as NodeInput,
  URLPatternOptions as NodeOptions,
} from "node:url";
declare global {
  type URLPatternInput = NodeInput;
  type URLPatternOptions = NodeOptions;
}
