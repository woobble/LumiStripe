//! JSON input coercions preserved from FastAPI/Pydantic request models.

use serde::{Deserialize, Deserializer};
use serde_json::Value;

pub(crate) const INVALID_PYDANTIC_BOOL_PREFIX: &str = "lumistripe_pydantic_bool_invalid=";

pub(crate) fn deserialize_pydantic_bool<'de, D>(deserializer: D) -> Result<bool, D::Error>
where
    D: Deserializer<'de>,
{
    let value = Value::deserialize(deserializer)?;
    parse_pydantic_bool(value.clone())
        .ok_or_else(|| serde::de::Error::custom(format!("{INVALID_PYDANTIC_BOOL_PREFIX}{value}")))
}

pub(crate) fn deserialize_optional_pydantic_bool<'de, D>(
    deserializer: D,
) -> Result<Option<bool>, D::Error>
where
    D: Deserializer<'de>,
{
    let value = Option::<Value>::deserialize(deserializer)?;
    value
        .map(|value| {
            parse_pydantic_bool(value.clone()).ok_or_else(|| {
                serde::de::Error::custom(format!("{INVALID_PYDANTIC_BOOL_PREFIX}{value}"))
            })
        })
        .transpose()
}

pub(crate) fn pydantic_bool_error(body_text: &str) -> Option<(Vec<Value>, Value)> {
    let marker = body_text.find(INVALID_PYDANTIC_BOOL_PREFIX)?;
    let prefix = body_text[..marker].trim_end().strip_suffix(':')?.trim_end();
    let field_path = prefix.rsplit_once(": ")?.1.trim();
    if field_path.is_empty() {
        return None;
    }

    let serialized_input = body_text[marker + INVALID_PYDANTIC_BOOL_PREFIX.len()..]
        .rsplit_once(" at line ")?
        .0;
    let input = serde_json::from_str(serialized_input).ok()?;
    let bytes = field_path.as_bytes();
    let mut path = Vec::new();
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'.' => index += 1,
            b'[' => {
                index += 1;
                let start = index;
                while index < bytes.len() && bytes[index].is_ascii_digit() {
                    index += 1;
                }
                if start == index || bytes.get(index) != Some(&b']') {
                    return None;
                }
                let item_index = field_path[start..index].parse::<usize>().ok()?;
                path.push(serde_json::json!(item_index));
                index += 1;
            }
            _ => {
                let start = index;
                while index < bytes.len() && !matches!(bytes[index], b'.' | b'[') {
                    index += 1;
                }
                path.push(serde_json::json!(&field_path[start..index]));
            }
        }
    }
    Some((path, input))
}

fn parse_pydantic_bool(value: Value) -> Option<bool> {
    match value {
        Value::Bool(value) => Some(value),
        Value::Number(number) => match number.as_f64()? {
            0.0 => Some(false),
            1.0 => Some(true),
            _ => None,
        },
        Value::String(value) => match value.to_ascii_lowercase().as_str() {
            "1" | "true" | "t" | "yes" | "y" | "on" => Some(true),
            "0" | "false" | "f" | "no" | "n" | "off" => Some(false),
            _ => None,
        },
        Value::Null | Value::Array(_) | Value::Object(_) => None,
    }
}

#[cfg(test)]
mod tests {
    use super::{
        deserialize_optional_pydantic_bool, deserialize_pydantic_bool, pydantic_bool_error,
    };
    use serde::Deserialize;
    use serde_json::{Value, json};

    #[derive(Deserialize)]
    struct RequiredBool {
        #[serde(deserialize_with = "deserialize_pydantic_bool")]
        value: bool,
    }

    #[derive(Deserialize)]
    struct OptionalBool {
        #[serde(default, deserialize_with = "deserialize_optional_pydantic_bool")]
        value: Option<bool>,
    }

    #[test]
    fn required_boolean_matches_pydantic_json_coercions() {
        for (value, expected) in [
            (json!(true), true),
            (json!(false), false),
            (json!(1), true),
            (json!(0), false),
            (json!(1.0), true),
            (json!(0.0), false),
            (json!("true"), true),
            (json!("YES"), true),
            (json!("on"), true),
            (json!("t"), true),
            (json!("1"), true),
            (json!("false"), false),
            (json!("NO"), false),
            (json!("off"), false),
            (json!("f"), false),
            (json!("0"), false),
        ] {
            let parsed: RequiredBool = serde_json::from_value(json!({"value": value}))
                .expect("Pydantic accepts this boolean input");
            assert_eq!(parsed.value, expected, "input {value}");
        }
    }

    #[test]
    fn required_boolean_rejects_values_pydantic_rejects() {
        for value in [
            json!(2),
            json!(-1),
            json!(0.5),
            json!(" true"),
            json!("2"),
            Value::Null,
            json!([]),
            json!({}),
        ] {
            assert!(
                serde_json::from_value::<RequiredBool>(json!({"value": value})).is_err(),
                "input {value} should not coerce to a boolean"
            );
        }
    }

    #[test]
    fn optional_boolean_preserves_null_and_missing_values() {
        let explicit_null: OptionalBool = serde_json::from_value(json!({"value": null})).unwrap();
        let missing: OptionalBool = serde_json::from_value(json!({})).unwrap();
        let coerced: OptionalBool = serde_json::from_value(json!({"value": "yes"})).unwrap();

        assert_eq!(explicit_null.value, None);
        assert_eq!(missing.value, None);
        assert_eq!(coerced.value, Some(true));
    }

    #[test]
    fn boolean_rejection_recovers_the_pydantic_field_path_and_input() {
        let body_text = concat!(
            "Failed to deserialize the JSON body into the target type: two_step: ",
            "lumistripe_pydantic_bool_invalid=\"invalid\" at line 1 column 47"
        );
        assert_eq!(
            pydantic_bool_error(body_text),
            Some((vec![json!("two_step")], json!("invalid")))
        );
    }

    #[test]
    fn boolean_rejection_parses_list_indices_in_field_paths() {
        let body_text = concat!(
            "Failed to deserialize the JSON body into the target type: outputs[0].reversed: ",
            "lumistripe_pydantic_bool_invalid=\"invalid\" at line 1 column 47"
        );
        assert_eq!(
            pydantic_bool_error(body_text),
            Some((
                vec![json!("outputs"), json!(0), json!("reversed")],
                json!("invalid")
            ))
        );
    }
}
