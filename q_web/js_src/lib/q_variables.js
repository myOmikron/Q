import {React} from "../react.js";
import ctx from "./q_ctx.js";
import TextInput from "./q_input.js";

export default class Variables extends React.Component {
    static contextType = ctx;

    constructor(props) {
        super(props);
    }

    checkKeyError(value) {
        let counter = 0;
        for(let key in this.props.value) {
            if(this.props.value[key]["key"] === value)
                counter++;
        }
        return value.includes(" ") || value === "" || counter >= 2
    }

    render() {
        let variables = [];
        for(let key in this.props.value) {
            variables.push(<tr>
                <td>
                    <TextInput className={this.checkKeyError(this.props.value[key]["key"]) ? "darkInput variableInput redBorder" : "darkInput variableInput"}
                               value={this.props.value[key]["key"]}
                               required="required"
                               onChange={(v) => {
                                   let tmp = this.props.value;
                                   tmp[key]["key"] = v;
                                   tmp[key]["faulty"] = this.checkKeyError(v);
                                   this.props.onChange(tmp);
                               }} />
                </td>
                <td>
                    <TextInput className="darkInput variableInput"
                               value={this.props.value[key]["value"]}
                               onChange={(v) => {
                                   let tmp = this.props.value;
                                   tmp[key]["value"] = v;
                                   this.props.onChange(tmp);
                               }} />
                </td>
                <td>
                    <img className="buttonImg"
                         src={this.context.static + "img/x.svg"}
                         alt="Delete"
                         onClick={(v) => {
                             let tmp = this.props.value;
                             delete tmp[key];
                             this.props.onChange(tmp);
                         }} />
                </td>
            </tr>);
        }

        return <div className="variableContent">
            <table className="variableTable">
                <tr>
                    <td>
                        <div className="variableInput">{variables.length > 0 ? "Key" : ""}</div>
                    </td>
                    <td>
                        <div className="variableInput">{variables.length > 0 ? "Value" : ""}</div>
                    </td>
                    <td>
                        <img className="buttonImg"
                             src={this.context.static + "img/plus.svg"}
                             alt="Add variable"
                             onClick={(v) => {
                                 let tmp = this.props.value;
                                 let max = Math.max(...Object.keys(this.props.value).map((v) => parseInt(v))) + 1;
                                 if (max === -Infinity) {
                                     max = 0;
                                 }
                                 tmp[max] = {
                                     "key": "",
                                     "value": "",
                                     "faulty": true,
                                 }
                                 this.props.onChange(tmp);
                             }} />
                    </td>
                </tr>
                {variables}
            </table>
        </div>;
    }
}
